# file: first-party/applications/experimentRunner/launcher.py ; version: 5
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
from threading import RLock, Thread

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
    schema.require(value['schemaId'] == 'actant.experiment.host@2', 'Unsupported host schema; use version 2.')
    aliases, limits = value['aliases'], value['limits']
    schema.require(type(aliases) is dict and bool(aliases), 'Missing host aliases.')
    for alias, selector in aliases.items():
        schema.name(alias)
        schema.name(selector)
    schema.fields(limits, ('maxTokens', 'timeoutSeconds', 'maxTurns', 'maxOutputCharacters',
                          'maxConfigurations', 'maxRepetitions', 'maxExecutions', 'maxTurnCalls'))
    schema.number(limits['maxTokens'], 1, 4096, integer=True)
    schema.number(limits['timeoutSeconds'], 0, 120, exclusive=True)
    schema.number(limits['maxTurns'], 2, 16, integer=True)
    schema.number(limits['maxOutputCharacters'], 1, 65536, integer=True)
    for key, maximum in (('maxConfigurations', 8), ('maxRepetitions', 10), ('maxExecutions', 32), ('maxTurnCalls', 256)):
        schema.number(limits[key], 1, maximum, integer=True)
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
    selectors = tuple(dict.fromkeys(host.value()['aliases'][config['profile']]
                                   for config in definition.value()['configurations']))
    caller = adapter.EntrySelector.fromSource('applications.experimentRunner', '0.1.0', 'main', APP / 'codeEntry.py')
    provider = adapter.EntrySelector.fromSource('llmDrivers.llamaCpp', '0.3.0', 'driver', DRIVER / 'structuredCodeEntry.py')
    composer = adapter.LlamaCppComposition(domainId='experiment-runner', catalog=catalog,
        providerEntries=(provider,), grants=(adapter.LlamaCppGrant(caller, selectors, allowTransition=True),))
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
    path = directory / (result['suite']['suiteId'] + '.json')
    encoded = schema.canonical(result)
    schema.require(len(encoded) <= schema.MAX_RESULT_BYTES, 'Private suite result exceeds byte limit.')
    io.writeTextAtomic(path, encoded.decode('utf-8'))
    return path


class _CancellationFlag:
    """Monotonic Python-dispatch signal flag without Event's non-reentrant condition lock."""

    def __init__(self):
        """Create an unset flag; resetting a cancelled suite is deliberately unsupported."""
        self._requested = False

    def set(self):
        """Publish cancellation with an idempotent attribute store, without taking locks."""
        self._requested = True

    def is_set(self):
        """Read the monotonic flag without a lock or callback into Job/provider code."""
        return self._requested


class SuiteControl:
    """Own suite cancellation and only the exact currently active Job, across unit gaps."""

    def __init__(self):
        """Create an initially uncancelled suite controller without runtime authority."""
        self._cancelled, self._lane, self._job = _CancellationFlag(), RLock(), None

    @property
    def requested(self):
        """Report cancellation independently of any completed or not-yet-created Job."""
        return self._cancelled.is_set()

    def cancel(self):
        """Cancel from ordinary threads; Job callbacks never run inside the controller lane."""
        self.requestSignalCancellation()
        with self._lane:
            job = self._job
        if job is not None:
            job.requestCancellation()

    def requestSignalCancellation(self):
        """Python SIGINT handler leaf: set a flag only, never acquire locks or call Job code."""
        self._cancelled.set()

    def begin(self, job):
        """Atomically reject pre-unit cancellation or select this exact pending Job."""
        with self._lane:
            if self.requested:
                return False
            if self._job is not None:
                raise RuntimeError('A conversation Job is already active.')
            self._job = job
            # Reentry can cancel after the first check but before publication.
            # Reject that gap cancellation rather than publishing uncancelled work.
            if self.requested:
                self._job = None
            else:
                return True
        job.requestCancellation()
        return False

    def end(self, job):
        """Drop only this completed unit's handle, preserving cancellation between units."""
        with self._lane:
            if self._job is job:
                self._job = None


class _SignalCancellationRelay:
    """Deliver a signal flag to the exact active Job outside the interrupted Python stack."""

    def __init__(self, control):
        """Prepare an inert relay so its owner can retain it before starting the thread."""
        self._control, self._stopping, self._failed = control, False, False
        self._thread = Thread(target=self._deliver, name='experiment-cancellation', daemon=True)

    def start(self):
        """Start only after the launcher has rooted this relay in its cleanup scope."""
        self._thread.start()

    def _deliver(self):
        """Poll the monotonic flag, then invoke ordinary cooperative cancellation off-stack."""
        try:
            while not self._stopping:
                if self._control.requested:
                    self._control.cancel()
                    return
                time.sleep(0.01)
        except BaseException:
            self._failed = True  # Never export raw callback failures through thread logging.

    def close(self):
        """Stop and boundedly join this relay; failure supplies no provider-cleanup proof."""
        self._stopping = True
        if self._thread.ident is not None:
            self._thread.join(timeout=1)
        if self._thread.is_alive() or self._failed:
            raise RuntimeError('Suite cancellation relay did not close normally.')


def unitStatus(unit):
    """Project conversation success only after Job, calls, reservation and lifetime boundary."""
    run = unit['run']
    if unit['jobOutcome'] == 'Cancelled':
        return 'cancelled'
    if (unit['jobOutcome'] == 'Succeeded' and run['status'] == 'succeeded'
            and run['reservation']['close'] == 'queued' and unit['lifetimeBoundary'] == 'completed'):
        return 'succeeded'
    return 'failed'


def execute(prepared, resultsDirectory, *, job=None, control=None, handleInterrupt=True):
    """Execute config-major independent conversations, draining each Job before the next.

    R1's enclosing owned teardown and deferred first interrupt protect aggregate
    diagnostics/persistence too. No public session-release receipt is invented;
    queued close and the returned runtime lifetime boundary are recorded separately.
    """
    definition, hostConfig, composer = prepared
    config = hostConfig.value()
    control = SuiteControl() if control is None else control
    if job is not None and job.cancellationRequested:
        control.cancel()
    io = ManagedIo()
    suite = results.initialSuite(definition, schema.unitDefinition)
    startedNs = time.monotonic_ns()
    envelope = {'schemaId': 'actant.experiment.execution@3', 'suite': suite,
                'launcherFailure': None, 'signalCleanupFailures': [],
                'resultWriteFailure': None, 'hostCleanup': 'not-started'}
    resultPath = None
    host, runtime = None, None
    temporary = tempfile.TemporaryDirectory(prefix='actant-experiment-')
    stage, pendingExit = 'activation', None

    def noteInterrupt(error, at):
        """Keep the first non-Exception exit for re-raise after exact owned cleanup."""
        nonlocal pendingExit
        if not isinstance(error, Exception):
            if pendingExit is None:
                pendingExit = error
            if envelope['launcherFailure'] is None:
                envelope['launcherFailure'] = {'category': 'launcher-interrupted', 'stage': at}

    def launcherFailure(error, at):
        """Retain only bounded phase/category diagnostics, never raw exception messages."""
        noteInterrupt(error, at)
        if envelope['launcherFailure'] is None:
            envelope['launcherFailure'] = {'category': 'launcher-failed', 'stage': at}

    def signalCleanupFailure(error, operation):
        """Record each secondary signal-cleanup fault without replacing the captured primary exit."""
        envelope['signalCleanupFailures'].append({
            'category': 'interrupted' if not isinstance(error, Exception) else 'failed',
            'operation': operation})
        launcherFailure(error, 'signal-cleanup')

    try:
        try:
            try:
                host = RuntimeHost(applicationStore=ApplicationStore(Path(temporary.name) / 'saves'),
                    packResolver=PackResolver(roots=(DRIVER, APP)), providerComposers=(composer,))
                host.start()
                runtime = host.createApplication(appPackId='applications.experimentRunner',
                    plan=ManualActivationPlan(packIds=('llmDrivers.llamaCpp', 'applications.experimentRunner')),
                    config={'llamaCpp': config['llamaCpp'], 'experimentRunner': policyFor(hostConfig)})
                reader = runtime.invokeCapability('experimentRunner.reader@1')
                previousHandler, handlerRestorationNeeded, relay = None, False, None
                try:
                    if handleInterrupt:
                        relay = _SignalCancellationRelay(control)
                        stage = 'relay-start'
                        relay.start()

                        def cancel(signum, frame):
                            """Set only the suite flag; the owned relay delivers active-Job cancellation."""
                            control.requestSignalCancellation()
                        stage = 'signal-handler-installation'
                        previousHandler = signal.getsignal(signal.SIGINT)
                        # Own restoration before installation: signal.signal can
                        # take effect and then be interrupted before returning.
                        handlerRestorationNeeded = True
                        signal.signal(signal.SIGINT, cancel)
                    for unit in suite['units']:
                        stage = 'unit-admission'
                        if control.requested:
                            break
                        activeJob = job if unit['index'] == 1 and job is not None else Job.new()
                        if not control.begin(activeJob):
                            break
                        if control.requested:
                            control.end(activeJob)
                            break
                        runId, admitted, finished = None, False, False
                        try:
                            stage = 'preparation'
                            unitDefinition = schema.unitDefinition(definition, unit['configurationIndex'] - 1)
                            runId = runtime.invokeCapability('experimentRunner.prepare@1',
                                {'unitId': unit['unitId'], 'definition': unitDefinition.value()})
                            unit['status'] = 'running'
                            stage = 'job'
                            admitted = True
                            try:
                                runtime.runJob('experimentRunner.run@1', runId, job=activeJob)
                            except BaseException:
                                unit['lifetimeBoundary'] = 'failed-or-unknown'
                                raise
                            else:
                                unit['lifetimeBoundary'] = 'failed-or-unknown' if runtime.lastLlmCleanup else 'completed'
                            stage = 'diagnostic-finalization'
                            if unit['lifetimeBoundary'] == 'completed':
                                unit['run'] = runtime.invokeCapability('experimentRunner.finish@1',
                                    {'runId': runId, 'jobOutcome': activeJob.state.value}).value()
                                finished = True
                        except BaseException as error:
                            launcherFailure(error, stage)
                        finally:
                            if admitted:
                                unit['jobOutcome'] = activeJob.state.value
                            if runId is not None and not finished:
                                try:
                                    recorded = reader.read().value()
                                    recorded['status'] = ('cancelled' if activeJob.state.value == 'Cancelled' else
                                        'succeeded' if activeJob.state.value == 'Succeeded'
                                        and recorded['applicationOutcome'] == 'completed'
                                        and recorded['reservation']['close'] == 'queued' else 'failed')
                                    unit['run'] = recorded
                                except BaseException as error:
                                    noteInterrupt(error, 'diagnostic-inspection')
                            if unit['run']['status'] is None:
                                unit['run']['status'] = 'failed'
                            unit['status'] = unitStatus(unit)
                            if config.get('persistConfigurationEvidence', False):
                                try:
                                    selector = config['aliases'][unit['profileAlias']]
                                    evidence = plain(composer.configurationEvidence(host.executionResources, selector))
                                    unit['configurationEvidence'] = (evidence if len(schema.canonical(evidence)) <= 262144
                                        else {'status': 'unavailable-over-limit'})
                                except BaseException as error:
                                    unit['configurationEvidence'] = {'status': 'unavailable'}
                                    noteInterrupt(error, 'configuration-evidence')
                            control.end(activeJob)
                        if unit['status'] != 'succeeded' or pendingExit is not None or envelope['launcherFailure'] is not None:
                            break
                except BaseException as error:
                    # Capture the actual primary BEFORE either cleanup action can
                    # raise. The outer handler may otherwise see only its replacement.
                    launcherFailure(error, stage)
                    raise
                finally:
                    stage = 'signal-cleanup'
                    if handlerRestorationNeeded:
                        try:
                            signal.signal(signal.SIGINT, previousHandler)
                        except BaseException as error:
                            signalCleanupFailure(error, 'handler-restoration')
                    if relay is not None:
                        try:
                            relay.close()
                        except BaseException as error:
                            signalCleanupFailure(error, 'relay-close')
            except BaseException as error:
                launcherFailure(error, stage)
            finally:
                suite['status'] = ('cancelled' if control.requested or any(unit['status'] == 'cancelled' for unit in suite['units']) else
                    'succeeded' if all(unit['status'] == 'succeeded' for unit in suite['units']) else 'failed')
                suite.update(endedTimeNs=time.time_ns(), durationNs=time.monotonic_ns() - startedNs)
                try:
                    resultPath = persist(io, resultsDirectory, envelope)
                except BaseException as error:
                    envelope['resultWriteFailure'] = {'category': 'result-write-failed', 'stage': 'before-teardown'}
                    noteInterrupt(error, 'before-teardown-write')
        finally:
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
        launcherFailure(error, 'diagnostic-processing')
        if pendingExit is None:
            pendingExit = error
    finally:
        try:
            temporary.cleanup()
        except BaseException as error:
            launcherFailure(error, 'temporary-cleanup')
    snapshot = schema.Snapshot(schema.canonical(envelope))
    if pendingExit is not None:
        pendingExit.experimentExecution = snapshot
        raise pendingExit
    return snapshot, resultPath


def exitCode(envelope):
    """Require complete suite success and independent launcher/persistence/host success."""
    return 0 if (envelope['suite']['status'] == 'succeeded' and envelope['launcherFailure'] is None
                 and envelope['resultWriteFailure'] is None and envelope['hostCleanup'] == 'completed') else 1
