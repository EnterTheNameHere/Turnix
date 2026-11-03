# file: first-party/applications/experimentRunner/launcher.py ; version: 2
"""Trusted host composition and private per-execution persistence for EX-01/02."""
from __future__ import annotations

import importlib
import importlib.util
import json
import signal
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from backend.io.managedIo import ManagedIo
from backend.orchestration import Job
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore

schema = importlib.import_module('first-party.applications.experimentRunner.definition')
results = importlib.import_module('first-party.applications.experimentRunner.results')
APP = Path(__file__).resolve().parent
ROOT = APP.parents[2]
DRIVER = ROOT / 'first-party/llmDrivers/llamaCpp'


def loadAdapter():
    """Load only the repository's fixed first-party trusted composition module."""
    moduleName = 'experimentRunnerTrustedLlamaCpp'
    if moduleName not in sys.modules:
        spec = importlib.util.spec_from_file_location(moduleName, DRIVER / 'executionAdapter.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[moduleName] = module
        spec.loader.exec_module(module)
    return sys.modules[moduleName]


def readJson(path):
    """Cap actual read bytes before parsing, including files that grow during reading."""
    with Path(path).open('rb') as source:
        return schema.decode(source.read(schema.MAX_BYTES + 1))


def hostConfiguration(value):
    """Freeze trusted launch config separately from untrusted declarative experiments."""
    value = schema.decode(schema.canonical(value))
    schema.fields(value, ('schemaId', 'llamaCpp', 'aliases', 'limits'), ('persistConfigurationEvidence',))
    schema.require(value['schemaId'] == 'actant.experiment.host@1', 'Unsupported host schema.')
    aliases, limits = value['aliases'], value['limits']
    schema.require(type(aliases) is dict and bool(aliases), 'Missing host aliases.')
    for alias, selector in aliases.items():
        schema.name(alias)
        schema.name(selector)
    schema.fields(limits, ('maxTokens', 'timeoutSeconds', 'maxTurns', 'maxOutputCharacters'))
    schema.number(limits['maxTokens'], 1, 4096, integer=True)
    schema.number(limits['timeoutSeconds'], 0, 120, exclusive=True)
    schema.number(limits['maxTurns'], 2, 16, integer=True)
    schema.number(limits['maxOutputCharacters'], 1, 65536, integer=True)
    schema.require(type(value.get('persistConfigurationEvidence', False)) is bool, 'Invalid evidence selection.')
    schema.require(type(value['llamaCpp']) is dict and value['llamaCpp'].get('manageServer') is True,
                   'Only trusted managed llama.cpp is supported.')
    return schema.Snapshot(schema.canonical(value))


def policyFor(host):
    """Project only frozen aliases and request/output limits into Application config."""
    value = host.value()
    return {'aliases': value['aliases'], **value['limits']}


def prepare(definitionValue, hostValue):
    """Validate every turn and selector before constructing or activating a runtime."""
    host = hostConfiguration(hostValue)
    definition = schema.validate(definitionValue, policyFor(host))
    adapter = loadAdapter()
    catalog = adapter.LlamaCppCatalog.fromConfig(host.value()['llamaCpp'])
    schema.require(all(selector in catalog.profiles for selector in host.value()['aliases'].values()),
                   'Host alias references an unavailable catalog selector.')
    selector = host.value()['aliases'][definition.value()['configurations'][0]['profile']]
    caller = adapter.EntrySelector.fromSource('applications.experimentRunner', '0.1.0', 'main', APP / 'codeEntry.py')
    provider = adapter.EntrySelector.fromSource('llmDrivers.llamaCpp', '0.3.0', 'driver', DRIVER / 'structuredCodeEntry.py')
    composer = adapter.LlamaCppComposition(domainId='experiment-runner', catalog=catalog,
        providerEntries=(provider,), grants=(adapter.LlamaCppGrant(caller, (selector,), allowTransition=True),))
    return definition, host, composer


def plain(value):
    """Detach known immutable JSON containers without persisting live authority."""
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise TypeError('Non-JSON private evidence.')


def persist(io, directory, result):
    """Atomically write one opaque-run file into an explicitly selected private directory."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (result['run']['runId'] + '.json')
    io.writeJsonAtomic(path, result)
    return path


def execute(prepared, resultsDirectory, *, job=None, handleInterrupt=True):
    """Run one actual Application Job; preserve its outcome through launcher/write failures.

    The initial private write precedes teardown. A second atomic update records host
    cleanup when possible. Neither persistence nor shutdown changes original Job or
    turn outcomes. Non-Exception exits are re-raised only after owned teardown;
    their experimentExecution attribute retains the immutable diagnostic envelope
    when a private write was interrupted. No failed write is retried.
    """
    definition, hostConfig, composer = prepared
    config = hostConfig.value()
    io = ManagedIo()
    job = Job.new() if job is None else job
    fallback = results.initialResult(definition)
    startedNs = time.monotonic_ns()
    envelope = {'schemaId': 'actant.experiment.execution@1', 'run': None, 'jobOutcome': None,
                'launcherFailure': None, 'resultWriteFailure': None, 'hostCleanup': 'not-started',
                'configurationEvidence': None}
    resultPath = None
    host, runtime, runId = None, None, None
    temporary = tempfile.TemporaryDirectory(prefix='actant-experiment-')
    working = temporary.name
    stage = 'activation'
    pendingExit = None

    def noteInterrupt(error, at):
        """Defer non-Exception exits without disguising them as successful execution."""
        nonlocal pendingExit
        if not isinstance(error, Exception):
            if pendingExit is None:
                pendingExit = error
            if envelope['launcherFailure'] is None:
                envelope['launcherFailure'] = {'category': 'launcher-interrupted', 'stage': at}

    try:
        try:
            try:
                host = RuntimeHost(applicationStore=ApplicationStore(Path(working) / 'saves'),
                    packResolver=PackResolver(roots=(DRIVER, APP)), providerComposers=(composer,))
                host.start()
                runtime = host.createApplication(appPackId='applications.experimentRunner',
                    plan=ManualActivationPlan(packIds=('llmDrivers.llamaCpp', 'applications.experimentRunner')),
                    config={'llamaCpp': config['llamaCpp'], 'experimentRunner': policyFor(hostConfig)})
                stage = 'preparation'
                runId = runtime.invokeCapability('experimentRunner.prepare@1', definition.value())
                previousHandler = None
                if handleInterrupt:
                    def cancel(signum, frame):
                        """Request cooperative cancellation on the retained exact Job handle."""
                        job.requestCancellation()
                    previousHandler = signal.signal(signal.SIGINT, cancel)
                try:
                    stage = 'job'
                    runtime.runJob('experimentRunner.run@1', runId, job=job)
                finally:
                    if handleInterrupt:
                        signal.signal(signal.SIGINT, previousHandler)
                envelope['jobOutcome'] = job.state.value
                stage = 'diagnostic-finalization'
                envelope['run'] = runtime.invokeCapability('experimentRunner.finish@1',
                    {'runId': runId, 'jobOutcome': job.state.value}).value()
            except BaseException as error:
                noteInterrupt(error, stage)
                if envelope['launcherFailure'] is None:
                    envelope['launcherFailure'] = {'category': 'launcher-failed', 'stage': stage}
                envelope['jobOutcome'] = job.state.value
            finally:
                if runtime is not None and runId is not None and envelope['run'] is None:
                    try:
                        recorded = runtime.invokeCapability('experimentRunner.inspect@1').value()
                        recorded['status'] = ('cancelled' if job.state.value == 'Cancelled' else
                                              'succeeded' if job.state.value == 'Succeeded'
                                              and recorded['applicationOutcome'] == 'completed' else 'failed')
                        envelope['run'] = recorded
                    except BaseException as error:
                        noteInterrupt(error, 'diagnostic-inspection')
                if envelope['run'] is None:
                    envelope['run'] = fallback
                    envelope['run']['status'] = 'failed'
                    envelope['run'].update(endedTimeNs=time.time_ns(), durationNs=time.monotonic_ns() - startedNs)
                if config.get('persistConfigurationEvidence', False) and host is not None:
                    try:
                        selector = config['aliases'][definition.value()['configurations'][0]['profile']]
                        envelope['configurationEvidence'] = plain(composer.configurationEvidence(host.executionResources, selector))
                    except BaseException as error:
                        envelope['configurationEvidence'] = {'status': 'unavailable'}
                        noteInterrupt(error, 'configuration-evidence')
                try:
                    resultPath = persist(io, resultsDirectory, envelope)
                except BaseException as error:
                    envelope['resultWriteFailure'] = {'category': 'result-write-failed', 'stage': 'before-teardown'}
                    noteInterrupt(error, 'before-teardown-write')
        finally:
            # This owner boundary encloses every activation, finalization, evidence
            # and initial-write path, including uncaught non-Exception exits.
            if host is not None:
                try:
                    host.stop()
                    envelope['hostCleanup'] = 'completed'
                except BaseException as error:
                    envelope['hostCleanup'] = 'failed-or-unknown'
                    noteInterrupt(error, 'host-cleanup')
        if resultPath is not None:
            try:
                persist(io, resultsDirectory, envelope)
            except BaseException as error:
                envelope['resultWriteFailure'] = {'category': 'result-write-failed', 'stage': 'after-teardown'}
                noteInterrupt(error, 'after-teardown-write')
    except BaseException as error:
        # Unexpected diagnostic construction errors must also retain any earlier
        # interruption; host cleanup above has already been attempted.
        noteInterrupt(error, 'diagnostic-processing')
        if envelope['launcherFailure'] is None:
            envelope['launcherFailure'] = {'category': 'launcher-failed', 'stage': 'diagnostic-processing'}
        if pendingExit is None:
            pendingExit = error
    finally:
        try:
            temporary.cleanup()
        except BaseException as error:
            noteInterrupt(error, 'temporary-cleanup')
            if envelope['launcherFailure'] is None:
                envelope['launcherFailure'] = {'category': 'launcher-failed', 'stage': 'temporary-cleanup'}
    snapshot = schema.Snapshot(schema.canonical(envelope))
    if pendingExit is not None:
        pendingExit.experimentExecution = snapshot
        raise pendingExit
    return snapshot, resultPath


def exitCode(envelope):
    """Project CLI success only when run, Job, persistence and host cleanup all succeed."""
    return 0 if (envelope['run'] is not None and envelope['run']['status'] == 'succeeded'
                 and envelope['jobOutcome'] == 'Succeeded' and envelope['launcherFailure'] is None
                 and envelope['resultWriteFailure'] is None and envelope['hostCleanup'] == 'completed') else 1
