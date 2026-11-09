# file: tests/first_party/experimentRunner/test_observations.py ; version: 2
"""Actual App/PM/G ownership with synthetic provider IO and deterministic expiry boundaries."""
import importlib
import json
from threading import Event

import pytest

from tests.first_party.experimentRunner.test_experimentRunner import rig, launcher, definition, execute
from tests.first_party.experimentRunner.test_archive import archivePath, readArchive

observations = launcher.observations
ofmt = observations.ofmt
backendObservation = importlib.import_module('backend.observation')


def host4(rig, *, live=False, enabled=True):
    """Select only synthetic provider t1 with physical hardware needs explicitly disabled."""
    host = rig[1]
    host['schemaId'] = 'actant.experiment.host@4'
    host['observation'] = observations.defaults()
    selected = dict(configurationIndex=1, repetition=1, turnId='t1')
    host['observation'].update(enabled=enabled, calls=[selected] if enabled else [])
    if live:
        host['observation']['live'].update(enabled=True, call=selected, allowedOrigin='http://127.0.0.1:8765', maxLifetimeSeconds=.25)
    return host


def snapshots(value):
    """Read actual typed snapshot artifact bytes through the accepted offline reader."""
    view = readArchive(value, includeContent=True)
    return [item['value'] for item in view['content'] if type(item['value']) is dict
            and item['value'].get('schemaId') == ofmt.fmt.PREFIX + 'observation@1']


def test_host3_keeps_archive1_and_host4_disabled_requests_nothing(rig, monkeypatch):
    """Explicit host4 opt-out performs no source requests, bridge activation or callback work."""
    host4(rig, enabled=False)
    def forbidden(*args, **kwargs):
        """Reject any accidental observation allocation on the disabled path."""
        raise AssertionError('unexpected request')
    monkeypatch.setattr(backendObservation.ObservationFacade, 'request', forbidden)
    value, _ = execute(rig)
    assert value['schemaId'] == 'actant.experiment.execution@5'
    view = readArchive(value)
    assert view['schemaId'] == 'actant.experiment.archive-view@2' and view['status'] == 'sealed'
    assert view['observations'] == {} and rig[0]['calls'] == 2


def test_run_capture_is_typed_private_and_independent_of_inference_outcomes(rig):
    """Same-owner one-use RUN session captures actual stream and releases before invalidation."""
    host4(rig)
    value, _ = execute(rig)
    assert value['suite']['status'] == 'succeeded', value
    assert launcher.exitCode(value) == 0
    captured = snapshots(value)
    assert len(captured) == 1 and captured[0]['availability'] == 'available'
    evidence = captured[0]
    assert evidence['sourceLifetime'] == 'run' and evidence['providerCallId'] is not None
    assert evidence['publicData']['stream']['characterCount'] == '7'
    assert evidence['publicData']['stream']['outcome'] == 'completed'
    assert evidence['publicData']['hardware'] == []
    assert 'publicationId' not in evidence['publicData']
    ofmt.observation(evidence)
    assert readArchive(value)['status'] == 'sealed'


def test_live_no_hook_does_not_activate_bridge_and_still_captures_run(rig, monkeypatch):
    """Plain CLI-compatible execution explicitly lacks a credential handoff, with no listener."""
    host4(rig, live=True)
    def forbidden(*args, **kwargs):
        """Fail any optional endpoint activation when no private hook exists."""
        raise AssertionError('unexpected bridge')
    monkeypatch.setattr(observations.bridge, 'ObservationBridge', forbidden)
    value, _ = execute(rig)
    assert value['suite']['status'] == 'succeeded'
    assert snapshots(value)[0]['sourceLifetime'] == 'run'


def test_live_expiry_during_active_call_preserves_capture_and_single_inference(rig, monkeypatch):
    """Expired viewer authority cannot erase the guarded active source or call-final snapshot."""
    host4(rig, live=True)
    started, done = Event(), Event()
    notices = []
    def handoff(notice):
        """Receive a transient private notice without recording/logging its contents."""
        notices.append(notice)
        started.set()
    def onCall(index):
        """Force exact expiry after actual transport attachment, before provider delivery."""
        if index == 0:
            assert started.wait(2)
            current = held[0]
            current.expire()
            assert current.active and current.capture.snapshot().sessionId
            assert current.viewer.revoked
            done.set()
    held = []
    original = observations.Controller.begin
    def begin(controller, ctx, port, turnId):
        """Retain only observation-only state for this explicit deterministic probe."""
        call = original(controller, ctx, port, turnId)
        if call is not None:
            held.append(call)
        return call
    monkeypatch.setattr(observations.Controller, 'begin', begin)
    rig[0]['onCall'] = onCall
    value, _ = execute(rig, observationHandoff=handoff)
    assert done.is_set() and value['suite']['status'] == 'succeeded', value
    assert rig[0]['calls'] == 2 and notices
    captured = snapshots(value)
    assert [s['phase'] for s in captured] == ['call-final', 'lifetime-final']
    assert captured[0]['availability'] == 'available'
    assert held[0].sessionRelease == 'completed'
    assert held[0].capture.revoked and held[0].viewer.revoked
    assert repr(notices[0]) == '<PrivateNotice>'


@pytest.mark.parametrize('boundary', ['setup', 'snapshot', 'snapshot-ack', 'tail'])
def test_expiry_guard_at_each_boundary_keeps_one_call_and_immutable_final(rig, monkeypatch, boundary):
    """Precise pre-attachment/copy/ACK/post-call expiry never erases the final call evidence."""
    host4(rig, live=True)
    reached = []
    if boundary == 'setup':
        original = observations.Call.setup
        def setup(call, ctx):
            """Expire after setup while the source has not yet reached facade attachment."""
            original(call, ctx)
            call.expire()
            assert call.active and call.capture.snapshot().sessionId
            reached.append(True)
        monkeypatch.setattr(observations.Call, 'setup', setup)
    elif boundary == 'snapshot':
        original = observations.Call.snapshot
        def snapshot(call):
            """Expire while copying final evidence, still inside the in-flight guard."""
            if call.active:
                call.expire()
                reached.append(True)
            return original(call)
        monkeypatch.setattr(observations.Call, 'snapshot', snapshot)
    elif boundary == 'snapshot-ack':
        original = observations.Call.writeSnapshot
        def checkpoint(call, phase, data):
            """Expire after immutable copy but before actual durable observation ACK."""
            if phase == 'call-final':
                assert data is not None and call.active
                call.expire()
                assert not call.capture.revoked
                reached.append(True)
            return original(call, phase, data)
        monkeypatch.setattr(observations.Call, 'writeSnapshot', checkpoint)
    else:
        original = observations.Call.__exit__
        def exitCall(call, *args):
            """Expire immediately after guard exit, retaining the already ACKed immutable copy."""
            result = original(call, *args)
            assert not call.active
            call.expire()
            reached.append(True)
            return result
        monkeypatch.setattr(observations.Call, '__exit__', exitCall)
    value, _ = execute(rig, observationHandoff=lambda notice: None)
    assert reached and value['suite']['status'] == 'succeeded' and rig[0]['calls'] == 2
    assert snapshots(value)[0]['availability'] == 'available'
    assert readArchive(value)['status'] == 'sealed'


def test_optional_snapshot_failure_does_not_retry_or_relabel_inference(rig, monkeypatch):
    """An unavailable serializer is independent of genuine successful inference and cleanup."""
    host4(rig)
    def unavailable(*args):
        """Supply a private ordinary codec failure that must never escape diagnostics."""
        raise RuntimeError('PRIVATE_CODEC_SENTINEL')
    monkeypatch.setattr(observations.codec, 'serialize', unavailable)
    value, _ = execute(rig)
    assert value['suite']['status'] == 'succeeded' and rig[0]['calls'] == 2
    assert snapshots(value)[0]['availability'] == 'unavailable'
    assert 'PRIVATE_CODEC_SENTINEL' not in json.dumps(readArchive(value, includeContent=True))


@pytest.mark.parametrize('exception', [RuntimeError, KeyboardInterrupt])
def test_private_callback_failure_has_no_trace_or_job_error_leak(rig, exception):
    """Trusted callback exceptions are consumed as fixed handoff failure, outside the Job."""
    host4(rig, live=True)
    called = Event()
    def fail(notice):
        """Throw a deliberately credential-bearing callback error without logging it."""
        called.set()
        raise exception(notice.value()['connection']['readCredential'] + ' PRIVATE_CALLBACK_SENTINEL')
    value, _ = execute(rig, observationHandoff=fail)
    assert called.is_set() and value['suite']['status'] == 'succeeded'
    assert 'PRIVATE_CALLBACK_SENTINEL' not in json.dumps(value) + json.dumps(readArchive(value, includeContent=True))


def test_stuck_handoff_is_pending_with_source_and_endpoint_revoked(rig):
    """A nonreturning private callback cannot delay inference or falsely claim worker closure."""
    host4(rig, live=True)
    blocked, entered = Event(), Event()
    def stuck(notice):
        """Hold only the transient notice, never Context, and release after assertions."""
        entered.set()
        blocked.wait(5)
    try:
        value, _ = execute(rig, observationHandoff=stuck)
        assert entered.is_set() and value['suite']['status'] == 'succeeded'
        artifacts = readArchive(value, includeContent=True)['content']
        cleanup = next(item['value'] for item in artifacts if type(item['value']) is dict and
                       item['value'].get('schemaId') == ofmt.fmt.PREFIX + 'observation-cleanup@1')
        assert cleanup['handoffWorkerClose'] == 'pending' and cleanup['sessionRelease'] == 'completed'
        assert cleanup['publicationWithdraw'] == 'completed'
    finally:
        blocked.set()


def rewriteV2(path, records):
    """Rehash intentionally malformed version2 streams without weakening original raw v1 tests."""
    fmt = ofmt.fmt
    previous = fmt.digest((path / 'archive-manifest.json').read_bytes())
    lines, terminal = [], None
    for sequence, record in enumerate(records, 1):
        if record['kind'] == 'suite-outcome':
            terminal = sequence
        if record['kind'] == 'archive-seal':
            record['payload']['terminalSequence'] = terminal
        record.update(sequence=sequence, previousCommitHash=previous)
        record['dataSha256'] = fmt.hashWithout(record, 'dataSha256')
        commit = dict(schemaId=fmt.PREFIX + 'archive-commit@2', sequence=sequence,
            dataSha256=record['dataSha256'], previousCommitHash=previous)
        commit['commitSha256'] = fmt.hashWithout(commit, 'commitSha256')
        lines.extend([fmt.canonical(record) + b'\n', fmt.canonical(commit) + b'\n'])
        previous = commit['commitSha256']
    (path / 'records.log').write_bytes(b''.join(lines))


@pytest.mark.parametrize('fault', ['body-history', 'not-sent', 'attempt-link', 'provider-link', 'clock-link',
                                  'metric-unit', 'wrong-record-version', 'missing-observation', 'cleanup-before-snapshot'])
def test_rehashed_archive2_faults_preserve_original_integrity_and_typed_links(rig, fault):
    """Valid recomputed checksums cannot bypass R1, versions or observation correlation."""
    host4(rig)
    value, _ = execute(rig)
    path = archivePath(value)
    records = [json.loads(line) for line in (path / 'records.log').read_bytes().splitlines()[::2]]
    if fault == 'body-history':
        records = [r for r in records if not (r['kind'] == 'request-evidence' and r['payload']['phase'] != 'final')]
    elif fault == 'not-sent':
        next(r for r in records if r['kind'] == 'request-evidence' and r['payload']['phase'] == 'final')['payload']['mayHaveSent'] = False
    elif fault == 'missing-observation':
        records = [r for r in records if r['kind'] not in ('observation-snapshot', 'observation-cleanup')]
    elif fault == 'cleanup-before-snapshot':
        a = next(i for i, r in enumerate(records) if r['kind'] == 'observation-snapshot')
        b = next(i for i, r in enumerate(records) if r['kind'] == 'observation-cleanup')
        records[a], records[b] = records[b], records[a]
    else:
        record = next(r for r in records if r['kind'] == 'observation-snapshot')
        if fault == 'wrong-record-version':
            record['schemaId'] = ofmt.fmt.PREFIX + 'archive-record@1'
        elif fault == 'attempt-link':
            record['payload']['attemptId'] = 'f' * 32
        else:
            ref = record['payload']['observationRef']
            blob = json.loads((path / 'artifacts' / (ref['sha256'] + '.blob')).read_bytes())
            if fault == 'provider-link':
                blob['providerCallId'] = 'f' * 32
            elif fault == 'clock-link':
                blob['clockEpochId'] = 'f' * 32
            else:
                blob['publicData']['units']['streamCharacters'] = 'tokens'
            raw = ofmt.fmt.canonical(blob)
            ref.update(bytes=len(raw), sha256=ofmt.fmt.digest(raw))
            record['artifacts'] = [ref]
            (path / 'artifacts' / (ref['sha256'] + '.blob')).write_bytes(raw)
    rewriteV2(path, records)
    view = readArchive(value)
    assert view['status'] == 'corrupt' and view['failureSequence'] == view['committedSequence'] + 1


def test_archive2_offline_report_pm_metrics_use_actual_call_clock_and_not_tail(rig):
    """Reports derive only call-final delivery metrics and retain absence for unselected calls."""
    host4(rig)
    value, _ = execute(rig)
    reporting = importlib.import_module('first-party.applications.experimentRunner.reporting')
    result = reporting.report(archivePath(value))
    reporting.reportSchema.validate(result)
    first, second = result['rows']
    assert first['metrics'][3]['value'] == '7' and second['metrics'][3]['value'] is None
    assert first['metrics'][3]['measurementProvenance']['clockEpochId'] == snapshots(value)[0]['clockEpochId']
    assert result['metrics'][3]['contributing'] == result['metrics'][3]['excluded'] == 1


def test_borrowed_hardware_claim_survives_app_source_and_host_cleanup(rig, monkeypatch):
    """App release removes only its own real telemetry claim, preserving an independent reader."""
    from backend.telemetry import TelemetryService, TelemetryConfiguration, TelemetryMode
    host4(rig)
    rig[1]['observation']['request']['gpu'] = True
    def noWorker(service):
        """Advance the real service manually at actual provider send boundaries."""
    monkeypatch.setattr(TelemetryService, '_ensureWorker', noWorker)
    service = TelemetryService(machineProvider=None, gpuProvider=None)
    peer = service.subscribe(TelemetryConfiguration(memoryMode=TelemetryMode.DISABLED, cpuMode=TelemetryMode.DISABLED,
        gpuMode=TelemetryMode.HISTORY, sampleIntervalSeconds=.25, historySamples=8))
    rig[0]['onCall'] = lambda index: service.sample()
    try:
        value, _ = execute(rig, telemetryService=service)
        assert value['suite']['status'] == 'succeeded'
        evidence = snapshots(value)[0]
        assert evidence['availability'] == 'available' and evidence['publicData']['hardware']
        assert peer.history and not peer.released
    finally:
        peer.release()
        service.stop()


def test_observed_generic_provider_error_is_never_retried_unobserved(rig):
    """An ambiguous actual provider failure remains one failed inference, never optional fallback."""
    host4(rig)
    rig[0]['streams'][0] = RuntimeError('PRIVATE_PROVIDER_SENTINEL')
    value, _ = execute(rig)
    assert rig[0]['calls'] == 1 and value['suite']['status'] == 'failed'
    assert value['suite']['units'][0]['jobOutcome'] == 'Failed'
    assert readArchive(value)['status'] == 'sealed'
    assert 'PRIVATE_PROVIDER_SENTINEL' not in json.dumps(value) + json.dumps(readArchive(value, includeContent=True))


def test_inference_first_exit_survives_secondary_observation_snapshot_failure(rig, monkeypatch):
    """The original synchronous first exit is re-raised after independent source/host cleanup."""
    host4(rig)
    first, second = KeyboardInterrupt('private primary'), SystemExit(17)
    from backend.context.codeEntryContext import _LlmFacade
    originalRun = _LlmFacade.run
    def infer(facade, **kwargs):
        """Leave all unobserved paths untouched; interrupt the selected guarded facade call."""
        if kwargs.get('observation') is not None:
            raise first
        return originalRun(facade, **kwargs)
    def snapshot(call):
        """Provide an independent secondary first-exit class during final snapshot cleanup."""
        raise second
    monkeypatch.setattr(_LlmFacade, 'run', infer)
    monkeypatch.setattr(observations.Call, 'snapshot', snapshot)
    with pytest.raises(KeyboardInterrupt) as caught:
        execute(rig)
    assert caught.value is first
    value = caught.value.experimentExecution.value()
    assert value['hostCleanup'] == 'completed'
    assert all(p.returncode is not None for p in rig[0]['processes'])


@pytest.mark.parametrize('unknown', [False, True])
def test_optional_bridge_clean_failure_is_unavailable_but_unknown_cleanup_is_critical(rig, monkeypatch, unknown):
    """Optional adoption cannot continue through an explicitly unknown activation cleanup state."""
    from backend.packs.runtime import PackLoader
    host4(rig, live=True)
    original = PackLoader.activatePack
    def activate(loader, pack):
        """Inject only the separately activated optional Pack after base composition completes."""
        if pack.packId == 'observation.bridge':
            if unknown:
                raise ExceptionGroup('private cleanup details', [RuntimeError('private')])
            raise RuntimeError('private clean activation failure')
        return original(loader, pack)
    monkeypatch.setattr(PackLoader, 'activatePack', activate)
    value, _ = execute(rig, observationHandoff=lambda notice: None)
    assert rig[0]['calls'] == (0 if unknown else 2)
    assert value['suite']['status'] == ('failed' if unknown else 'succeeded')


@pytest.mark.parametrize('mode', ['serialized', 'transport-attempt', 'response-headers-observed', 'final',
    'no-evidence', 'not-sent-without-headers', 'body-unavailable', 'headers-unavailable', 'false-with-response'])
def test_all_original_required_capture_recovery_faults_reject_on_actual_archive2(rig, mode):
    """Every original required-history R1 fault remains effective on correctly rehashed version2."""
    host4(rig)
    value, _ = execute(rig)
    path = archivePath(value)
    records = [json.loads(line) for line in (path / 'records.log').read_bytes().splitlines()[::2]]
    unitId = value['suite']['units'][0]['unitId']
    changed = []
    for record in records:
        selected = record['unitId'] == unitId and record['turnId'] == 't1'
        if selected and record['kind'] == 'request-evidence':
            phase = record['payload']['phase']
            if phase == mode or mode == 'no-evidence' or (mode == 'not-sent-without-headers' and phase == 'response-headers-observed'):
                continue
            if phase == 'final':
                if mode in ('not-sent-without-headers', 'headers-unavailable'):
                    record['payload'].update(mayHaveSent=False if mode == 'not-sent-without-headers' else True,
                                             responseHeadersObserved=None, responseBodyObserved=None)
                elif mode == 'body-unavailable':
                    record['payload']['responseBodyObserved'] = None
                elif mode == 'false-with-response':
                    record['payload']['mayHaveSent'] = False
        if selected and record['kind'] == 'partial' and mode in ('no-evidence', 'not-sent-without-headers'):
            continue
        changed.append(record)
    rewriteV2(path, changed)
    view = readArchive(value)
    selectedView = readArchive(value, unitId=unitId, turnId='t1')
    assert view['status'] == selectedView['status'] == 'corrupt'
    assert view['committedSequence'] == selectedView['committedSequence']
    assert view['lastObservedSuiteStatus'] is None
    assert selectedView['units'][0]['turns'][0]['lastObservedStatus'] == 'planned'


def test_archive2_fresh_offline_report_and_original_body_comparison_need_no_backend(rig, monkeypatch):
    """Version dispatch and observation-derived metrics remain stdlib-only under an IO audit."""
    from tests.first_party.experimentRunner.test_archive import freshChild
    host4(rig)
    value, _ = execute(rig)
    path = archivePath(value)
    unit = value['suite']['units'][0]['unitId']
    code = r'''
import sys, importlib
sys.dont_write_bytecode=True
r=importlib.import_module('first-party.applications.experimentRunner.reporting')
e=importlib.import_module('first-party.applications.experimentRunner.exporting')
importlib.import_module('first-party.applications.experimentRunner.definition')
importlib.import_module('first-party.applications.experimentRunner.observationFormat')
def audit(event, args):
    """Deny active IO while allowing current source-code and archive reads."""
    if event.startswith(('socket.', 'subprocess.')):
        raise AssertionError('active IO')
    if event == 'open':
        mode, flags=args[1:3]
        if isinstance(mode,str) and any(c in mode for c in 'wax+') or isinstance(flags,int) and flags & 3:
            raise AssertionError('write')
sys.addaudithook(audit)
selected=sys.argv[1:4]
v=r.report(selected[0])
r.reportSchema.validate(v)
assert v['rows'][0]['metrics'][3]['value']=='7'
assert r.compare(*selected,*selected,mode=r.MODES[2])['result']=='Equal'
assert not any(n=='backend' or n.startswith('backend.') for n in sys.modules)
'''
    child = freshChild(monkeypatch, code, path, unit, 't1')
    assert child.returncode == 0, child.stderr


def test_background_tail_first_exit_is_fixed_diagnostic_without_thread_trace(rig, monkeypatch):
    """An optional timer interruption cannot leak a raw thread exception or fail the next Job call."""
    import threading
    host4(rig, live=True)
    entered, traces = Event(), []
    original = observations.Call.snapshot
    def snapshot(call):
        """Interrupt only the background lifetime-final copy, after actual call-final retention."""
        if not call.active and not entered.is_set():
            entered.set()
            raise KeyboardInterrupt('PRIVATE_TAIL_SENTINEL')
        return original(call)
    def onCall(index):
        """Wait at the independent next actual send until the bounded first source expires."""
        if index == 1:
            assert entered.wait(3)
    monkeypatch.setattr(observations.Call, 'snapshot', snapshot)
    monkeypatch.setattr(threading, 'excepthook', lambda args: traces.append(args))
    rig[0]['onCall'] = onCall
    value, _ = execute(rig, observationHandoff=lambda notice: None)
    assert value['suite']['status'] == 'succeeded' and not traces
    assert snapshots(value)[0]['availability'] == 'available'
    content = json.dumps(readArchive(value, includeContent=True))
    assert 'PRIVATE_TAIL_SENTINEL' not in content and 'interrupted' in content
