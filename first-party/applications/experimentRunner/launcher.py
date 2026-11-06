# file: first-party/applications/experimentRunner/launcher.py ; version: 7
"""Trusted host composition and private per-execution persistence for EX-01/02."""
from __future__ import annotations

import importlib
import importlib.util
import hashlib
import os
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
from backend.orchestration.cancellation import ExecutionCancelled
from backend.llm.errors import LlmExecutionCleanupError, LlmExecutionOwnershipError
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore

schema = importlib.import_module('first-party.applications.experimentRunner.definition')
results = importlib.import_module('first-party.applications.experimentRunner.results')
archive = importlib.import_module('first-party.applications.experimentRunner.archive')
observations = importlib.import_module('first-party.applications.experimentRunner.observations')
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
    schema.fields(value, ('schemaId', 'llamaCpp', 'aliases', 'limits', 'archive'), ('persistConfigurationEvidence', 'observation'))
    schema.require(value['schemaId'] in ('actant.experiment.host@3', 'actant.experiment.host@4'), 'Unsupported host schema.')
    if value['schemaId'] == 'actant.experiment.host@4':
        observations.validate(value.get('observation'))
    else:
        schema.require('observation' not in value, 'Host3 has no observation policy.')
    archiveValue = value['archive']
    schema.fields(archiveValue, (*archive.fmt.DEFAULTS, 'capturePolicy', 'hashAssets'))
    archive.fmt.limits({key: archiveValue[key] for key in archive.fmt.DEFAULTS})
    schema.require(archiveValue['capturePolicy'] in ('required', 'disabled') and type(archiveValue['hashAssets']) is bool,
                   'Invalid archive capture policy.')
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
    if host.value()['schemaId'] == 'actant.experiment.host@4':
        observations.validate(host.value()['observation'], definition)
    archive.preflight(definition, policyFor(host) | {'captureRequired': host.value()['archive']['capturePolicy'] == 'required'},
        {k: host.value()['archive'][k] for k in archive.fmt.DEFAULTS},
        observation=host.value()['schemaId'] == 'actant.experiment.host@4' and host.value()['observation']['enabled'])
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


def privateConfiguration(value):
    """Keep bounded startup/build facts while omitting argv, environment and probe text.

    The provider's full private accessor may contain credentials in extraArgs.
    Exact inference body capture is separate from these startup observations.
    """
    launch = value.get('launch', {})
    build = value.get('build', {})
    requested = value.get('requested', {})
    reported = value.get('userReportedBuild') or {}
    controls = {}
    allowed = ('contextWindowTokens', 'threads', 'threadsBatch', 'gpuLayers', 'parallelSlots',
        'batchSize', 'ubatchSize', 'kvOffload', 'flashAttention', 'mmap', 'mlock', 'cpuMoeLayers', 'nCpuMoeLayers')
    for name in allowed:
        item = requested.get(name)
        if item is None or type(item) in (bool, int, float):
            controls[name] = item
    return dict(schemaId=value.get('schemaId'), profileId=value.get('profileId'), mode=value.get('mode'),
        requestedStartupControls=controls,
        launch=dict(status=launch.get('status'), generation=launch.get('generation'),
            actualArgvObserved=launch.get('actualArgv') is not None,
            plannedArgvObserved=launch.get('plannedArgv') is not None),
        build=dict(status=build.get('status'), sourceCommit=build.get('sourceCommit'), identityClaim=build.get('identityClaim')),
        userReportedBuild={key: reported.get(key) for key in ('version', 'build', 'commit', 'provenance')},
        providerReportedEffective={'status': 'unknown-unqueried'},
        rawLaunchInputs='omitted-private-policy')


def persist(io, directory, result):
    """Atomically write one opaque-run file into an explicitly selected private directory."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (result['suite']['suiteId'] + '.json')
    encoded = schema.canonical(result)
    schema.require(len(encoded) <= schema.MAX_RESULT_BYTES, 'Private suite result exceeds byte limit.')
    io.writeTextAtomic(path, encoded.decode('utf-8'))
    return path


def provenance(config):
    """Capture only selected source/asset identity once; no environment/header grants."""
    sources = []
    for path in (*sorted(APP.glob('*.py')), DRIVER / 'executionAdapter.py', DRIVER / 'requestEvidence.py',
                 DRIVER / 'codeEntry.py', DRIVER / 'structuredCodeEntry.py', DRIVER / 'ownedTransport.py'):
        raw = path.read_bytes()
        sources.append(dict(path=path.relative_to(ROOT).as_posix(), sha256=hashlib.sha256(raw).hexdigest(),
            versionHeader=raw.splitlines()[0].decode('utf-8')))
    assets = []
    selected = [(config['llamaCpp']['executable'], 'executable')]
    selected += [(v['modelPath'], 'model') for v in config['llamaCpp']['models'].values()]
    seen = set()
    for rawPath, role in selected:
        path = str(Path(rawPath).absolute())
        if (path, role) in seen:
            continue
        seen.add((path, role))
        fact = dict(path=path, role=role, sha256=None, status='unavailable-not-selected', observedTimeNs=time.time_ns())
        if config['archive']['hashAssets']:
            try:
                hasher = hashlib.sha256()
                with Path(path).open('rb') as source:
                    before = os.fstat(source.fileno())
                    while block := source.read(1024 * 1024):
                        hasher.update(block)
                    after = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                    fact.update(sha256=hasher.hexdigest(), status='hash-observed', bytes=after.st_size)
                else:
                    fact['status'] = 'unavailable-changed'
            except Exception:
                fact['status'] = 'unavailable'
        assets.append(fact)
    value = dict(platformBaseline='993e749aed93b240ea67532c73e7d27b612f3324', sources=sources, assets=assets,
        sourceObservation='file-bytes-observed-not-loaded-code-attestation',
        capturePolicy=config['archive']['capturePolicy'], serverConsumption='unknown', providerEffectiveState='unknown-unqueried',
        validationPolicy={'aliases': config['aliases'], **config['limits']})
    archive.fmt.require(len(schema.canonical(value)) <= 262144)
    return value


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

    def raiseIfCancelled(self):
        """Check suite and exact active Job cancellation outside writer/backend lanes."""
        with self._lane:
            job = self._job
        if self.requested or (job is not None and job.cancellationRequested):
            raise ExecutionCancelled('Experiment suite cancelled.')


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


def execute(prepared, resultsDirectory, *, job=None, control=None, handleInterrupt=True, observationHandoff=None, telemetryService=None):
    """Execute config-major independent conversations, draining each Job before the next.

    R1's enclosing owned teardown and deferred first interrupt protect aggregate
    diagnostics/persistence too. No public session-release receipt is invented;
    queued close and the returned runtime lifetime boundary are recorded separately.
    """
    definition, hostConfig, composer = prepared
    config = hostConfig.value()
    schema.require(observationHandoff is None or callable(observationHandoff), 'Invalid private observation hook.')
    observer = observations.Controller(config['observation'], observationHandoff) if config['schemaId'] == 'actant.experiment.host@4' else None
    control = SuiteControl() if control is None else control
    if job is not None and job.cancellationRequested:
        control.cancel()
    io = ManagedIo()
    suite = results.initialSuite(definition, schema.unitDefinition)
    startedNs = time.monotonic_ns()
    envelope = {'schemaId': 'actant.experiment.execution@4', 'suite': suite,
                'launcherFailure': None, 'signalCleanupFailures': [],
                'resultWriteFailure': None, 'hostCleanup': 'not-started',
                'archive': {'archiveId': None, 'path': None, 'failure': None, 'close': None}}
    if observer is not None:
        envelope['schemaId'] = 'actant.experiment.execution@5'
    resultPath = None
    host, runtime = None, None
    temporary = tempfile.TemporaryDirectory(prefix='actant-experiment-')
    stage, pendingExit = 'activation', None
    writer = archiveRun = None
    temporaryAttempted = archiveCloseAttempted = False

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

    def archiveFailure(error, at):
        """Keep persistence failure independent and retain first non-Exception precedence."""
        noteInterrupt(error, at)
        if envelope['archive']['failure'] is None:
            envelope['archive']['failure'] = dict(category='archive-failed', stage=at)
        if writer is not None:
            writer.fail(error)
            if writer.primary is not None:
                noteInterrupt(writer.primary, at)

    try:
        try:
            try:
                stage = 'archive-initial'
                caps = {key: config['archive'][key] for key in archive.fmt.DEFAULTS}
                writer = archive.ArchiveWriter(resultsDirectory, suite, caps, control.cancel, cancelled=control.raiseIfCancelled,
                    version=2 if observer is not None else 1)
                archiveRun = archive.ArchiveRun(writer, definition, composer, config)
                if observer is not None:
                    archiveRun.observationPolicy = observations.archivePolicy(config['observation'], observer.effective)
                envelope['archive'].update(archiveId=writer.archiveId, path=str(writer.path))
                adapter = loadAdapter()
                composer.requestEvidence = (adapter.PrivateRequestEvidence()
                    if config['archive']['capturePolicy'] == 'required' else None)
                composer.evidencePolicies = (() if composer.requestEvidence is None else
                    (adapter.RequestEvidencePolicy(composer.grants[0].entry, maxBodyBytes=caps['requestBytes']),))
                try:
                    archiveRun.initial(suite, provenance(config))
                except BaseException as error:
                    archiveFailure(error, stage)
                    raise
                stage = 'activation'
                host = RuntimeHost(applicationStore=ApplicationStore(Path(temporary.name) / 'saves'),
                    packResolver=PackResolver(roots=(DRIVER, APP, ROOT / 'first-party/observationBridge')),
                    providerComposers=(archiveRun,), telemetryService=telemetryService)
                host.start()
                runtime = host.createApplication(appPackId='applications.experimentRunner',
                    plan=ManualActivationPlan(packIds=('llmDrivers.llamaCpp', 'applications.experimentRunner')),
                    config={'llamaCpp': config['llamaCpp'], 'experimentRunner': policyFor(hostConfig),
                        'observationBridge': dict(host='127.0.0.1', port=0, allowedOrigins=[] if observer is None or
                            not config['observation']['live']['enabled'] else [config['observation']['live']['allowedOrigin']])})
                if observer is not None and observer.effective == 'pending-activation':
                    try:
                        resolver = PackResolver(roots=(ROOT / 'first-party/observationBridge',))
                        runtime.packLoader.activatePack(resolver.requireSingle('observation.bridge'))
                        observer.effective = 'available'
                    except Exception as error:
                        if isinstance(error, (BaseExceptionGroup, LlmExecutionCleanupError, LlmExecutionOwnershipError)) or getattr(error, 'llmCompositionCleanup', None) is not None:
                            raise RuntimeError('Observation activation cleanup is unknown.') from None
                        observer.effective = 'bridge-unavailable'
                    runtime.requireCompositionReady()
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
                        writer.check()
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
                            port = archiveRun.unit(unit, activeJob.jobId)
                            port.observationController = observer
                            unitDefinition = schema.unitDefinition(definition, unit['configurationIndex'] - 1)
                            runId = runtime.invokeCapability('experimentRunner.prepare@1',
                                {'unitId': unit['unitId'], 'definition': unitDefinition.value(), 'archivePort': port})
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
                                    unit['configurationEvidence'] = (privateConfiguration(evidence) if len(schema.canonical(evidence)) <= 262144
                                        else {'status': 'unavailable-over-limit'})
                                except BaseException as error:
                                    unit['configurationEvidence'] = {'status': 'unavailable'}
                                    noteInterrupt(error, 'configuration-evidence')
                            control.end(activeJob)
                            try:
                                if unit['unitId'] in archiveRun.ports:
                                    archiveRun.unitOutcome(unit)
                                writer.check()
                            except BaseException as error:
                                archiveFailure(error, 'unit-checkpoint')
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
                if archiveRun is not None:
                    try:
                        if observer is not None:
                            observer.finish(runtime)
                        archiveRun.suiteOutcome(suite)
                        archiveRun.cleanup('signal', 'failed-or-unknown' if envelope['signalCleanupFailures'] else 'completed',
                            dict(failures=envelope['signalCleanupFailures']))
                    except BaseException as error:
                        archiveFailure(error, 'suite-checkpoint')
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
            temporaryAttempted = True
            temporaryOutcome = 'completed'
            try:
                temporary.cleanup()
            except BaseException as error:
                temporaryOutcome = 'failed-or-unknown'
                launcherFailure(error, 'temporary-cleanup')
            if archiveRun is not None:
                try:
                    archiveRun.cleanup('host', envelope['hostCleanup'] if host is not None else 'unavailable',
                        dict(outcome=envelope['hostCleanup']))
                    archiveRun.cleanup('temporary', temporaryOutcome, dict(outcome=temporaryOutcome))
                    archiveRun.seal(suite['status'])
                except BaseException as error:
                    archiveFailure(error, 'archive-finalization')
            if writer is not None:
                archiveCloseAttempted = True
                try:
                    envelope['archive']['close'] = writer.close()
                    writer.check()
                except BaseException as error:
                    archiveFailure(error, 'archive-close')
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
        if not temporaryAttempted:
            try:
                temporary.cleanup()
            except BaseException as error:
                launcherFailure(error, 'temporary-cleanup')
        if writer is not None and not archiveCloseAttempted:
            try:
                envelope['archive']['close'] = writer.close()
                writer.check()
            except BaseException as error:
                archiveFailure(error, 'archive-close')
    snapshot = schema.Snapshot(schema.canonical(envelope))
    if pendingExit is not None:
        pendingExit.experimentExecution = snapshot
        raise pendingExit
    return snapshot, resultPath


def exitCode(envelope):
    """Require complete suite success and independent launcher/persistence/host success."""
    archiveOutcome = envelope.get('archive', {})
    close = archiveOutcome.get('close') or {}
    return 0 if (envelope['suite']['status'] == 'succeeded' and envelope['launcherFailure'] is None
                 and envelope['resultWriteFailure'] is None and envelope['hostCleanup'] == 'completed'
                 and archiveOutcome.get('failure') is None and close.get('status') == 'completed') else 1
