# file: tests/first_party/experimentRunner/test_archive.py ; version: 1
"""Actual disk checkpoints, fresh process death/recovery and managed archive failure gates."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import pytest

from tests.first_party.experimentRunner.test_experimentRunner import (
    rig, launcher, schema, definition, execute, executeSuite, suiteDefinition,
)

archive = launcher.archive
fmt = archive.fmt
reader = archive.reader
SECRET = 'PRIVATE_ARCHIVE_ERROR_7fb129'
REAL_POPEN = subprocess.Popen


def freshChild(monkeypatch, code, *args):
    """Launch only the exact model-free test child, restoring the synthetic IO fixture afterward."""
    with monkeypatch.context() as context:
        context.setattr(subprocess, 'Popen', REAL_POPEN)
        return subprocess.run([sys.executable, '-X', 'frozen_modules=off', '-c', code, *map(str, args)],
            cwd=launcher.ROOT, capture_output=True, text=True, timeout=10)


def archivePath(value):
    """Select the launcher-issued archive path, never a guessed live process."""
    return Path(value['archive']['path'])


def readArchive(value, **kwargs):
    """Inspect a completed or incomplete archive without runtime authority."""
    return reader.inspectArchive(archivePath(value), **kwargs)


def test_complete_private_archive_exact_wire_order_reasoning_and_read_only(rig):
    """All ACKed bodies and typed boundaries reopen without changing any file."""
    value, _ = execute(rig)
    before = {p.relative_to(archivePath(value)): p.read_bytes() for p in archivePath(value).rglob('*') if p.is_file()}
    view = readArchive(value, includeContent=True)
    assert view['status'] == 'sealed' and view['lastObservedSuiteStatus'] == 'succeeded'
    assert view['writerCloseObservation'] == view['ackObservation'] == 'unavailable'
    assert value['archive']['close']['status'] == 'completed'
    records = view['records']
    serialized = [r for r in records if r['kind'] == 'request-evidence' and r['payload']['phase'] == 'serialized']
    wire = [body for url, body in rig[0]['requestBytes'] if url.endswith('/completions')]
    bodies = [reader.BlobReader(archivePath(value), fmt.DEFAULTS)(r['payload']['bodyRef']) for r in serialized]
    assert bodies == wire and len(bodies) == 2
    assert json.loads(bodies[1])['messages'][1] == {'role': 'assistant', 'content': ' amber '}
    assert all(r['payload']['serverConsumption'] == 'unknown' for r in serialized)
    assert any(r['kind'] == 'partial' for r in records)
    kinds = [r['kind'] for r in records]
    assert kinds.index('turn-completed') < [i for i, k in enumerate(kinds) if k == 'turn-planned'][1]
    assert kinds[-1] == 'archive-seal' and kinds.index('unit-outcome') < kinds.index('suite-outcome')
    after = {p.relative_to(archivePath(value)): p.read_bytes() for p in archivePath(value).rglob('*') if p.is_file()}
    assert before == after


OFFLINE = r'''
import sys
sys.dont_write_bytecode = True
import importlib, json
m = importlib.import_module('first-party.applications.experimentRunner.archiveReader')
importlib.import_module('first-party.applications.experimentRunner.definition')
def audit(event, args):
    if event.startswith(('socket.', 'subprocess.')):
        raise AssertionError('unexpected active IO')
    if event == 'open':
        mode, flags = args[1:3]
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and flags & 3):
            raise AssertionError('unexpected write')
sys.addaudithook(audit)
v = m.inspectArchive(sys.argv[1])
assert not any(n == 'backend' or n.startswith('backend.') for n in sys.modules)
print(json.dumps({'status': v['status'], 'sequence': v['committedSequence'], 'outcome': v['lastObservedSuiteStatus']}))
'''


def test_fresh_offline_process_has_zero_writes_network_or_backend_imports(rig, monkeypatch):
    """Audit hooks deny writes/network/process creation during standalone recovery."""
    value, _ = execute(rig)
    child = freshChild(monkeypatch, OFFLINE, archivePath(value))
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)['status'] == 'sealed'


@pytest.mark.parametrize('tail,expected', [(b'{', 'torn-final-frame'), (b'\n', 'empty-final-frame')])
def test_torn_final_tail_preserves_committed_prefix(rig, tail, expected):
    """Even a sealed prefix plus torn tail is incomplete, never complete success."""
    value, _ = execute(rig)
    previous = readArchive(value)['committedSequence']
    with (archivePath(value) / 'records.log').open('ab') as target:
        target.write(tail)
    view = readArchive(value)
    assert view['status'] == 'incomplete' and view['tail'] == expected
    assert view['committedSequence'] == previous


@pytest.mark.parametrize('mode', ['json', 'chain', 'version', 'sequence', 'blob-missing', 'blob-corrupt'])
def test_complete_invalid_frame_or_blob_is_corruption_not_success(rig, mode):
    """Integrity/type failures preserve a useful prefix with explicit corruption."""
    value, _ = execute(rig)
    path = archivePath(value)
    lines = (path / 'records.log').read_bytes().splitlines(keepends=True)
    if mode.startswith('blob'):
        ref = json.loads(lines[0])['artifacts'][0]
        blob = path / 'artifacts' / (ref['sha256'] + '.blob')
        if mode == 'blob-missing':
            blob.unlink()
        else:
            blob.write_bytes(b'corrupt')
    else:
        if mode == 'json':
            lines[2] = b'{invalid}\n'
        else:
            data = json.loads(lines[2])
            if mode == 'chain':
                data['previousCommitHash'] = '0' * 64
            elif mode == 'version':
                data['schemaId'] = 'actant.experiment.archive-record@999'
            else:
                data['sequence'] = 900
            data['dataSha256'] = fmt.hashWithout(data, 'dataSha256')
            lines[2] = fmt.canonical(data) + b'\n'
        (path / 'records.log').write_bytes(b''.join(lines))
    view = readArchive(value)
    assert view['status'] == 'corrupt' and view['failureSequence'] is not None
    assert view['committedSequence'] <= 1


def test_final_data_without_commit_is_uncommitted_not_an_outcome(rig):
    """A complete final DATA line alone carries no committed observation."""
    value, _ = execute(rig)
    path = archivePath(value) / 'records.log'
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b''.join(lines[:-1]))
    view = readArchive(value)
    assert view['status'] == 'incomplete' and view['tail'] == 'uncommitted-data'
    assert view['records'][-1]['kind'] != 'archive-seal'


def test_turn_ack_survives_failed_job_rollback_and_no_second_send(rig, monkeypatch):
    """Failure after completed-turn ACK cannot erase that original successful turn."""
    original = archive.UnitArchivePort.completed
    def fail(port, result):
        """Throw only after the actual durable turn checkpoint returned."""
        original(port, result)
        raise RuntimeError('synthetic subsequent application failure')
    monkeypatch.setattr(archive.UnitArchivePort, 'completed', fail)
    value, _ = execute(rig)
    assert rig[0]['calls'] == 1 and value['suite']['units'][0]['jobOutcome'] == 'Failed'
    assert value['suite']['units'][0]['run']['turns'][0]['status'] == 'succeeded'
    view = readArchive(value)
    assert view['status'] == 'sealed' and view['lastObservedSuiteStatus'] == 'failed'
    assert len([r for r in view['records'] if r['kind'] == 'turn-completed']) == 1


@pytest.mark.parametrize('kind,expectedCalls', [('suite-planned', 0), ('unit-planned', 0), ('turn-planned', 0),
    ('request-evidence', 0), ('turn-completed', 1), ('unit-outcome', 2)])
def test_required_disk_failure_stops_further_admissions_and_has_fixed_diagnostics(rig, monkeypatch, kind, expectedCalls):
    """Initial/mid-run mandatory IO failure poisons future execution, never retries."""
    original = archive.ArchiveWriter._commit
    def fail(writer, command, journal):
        """Inject only the selected owned archive write boundary."""
        if command['kind'] == kind:
            raise OSError(SECRET)
        return original(writer, command, journal)
    monkeypatch.setattr(archive.ArchiveWriter, '_commit', fail)
    value, _ = execute(rig)
    assert rig[0]['calls'] == expectedCalls
    assert value['archive']['failure'] is not None and launcher.exitCode(value) == 1
    assert SECRET not in repr(value)
    if kind in ('suite-planned', 'unit-planned'):
        assert not rig[0]['processes']
    assert readArchive(value)['status'] == 'incomplete'


def test_initial_collision_does_not_overwrite_or_launch(rig, monkeypatch):
    """Exclusive archive identity is not a retry/overwrite mechanism."""
    existing = rig[2] / ('a' * 32)
    existing.mkdir(parents=True)
    (existing / 'sentinel').write_bytes(b'keep')
    class Identity:
        """Supply exactly one deterministic colliding archive identity."""
        hex = 'a' * 32
    monkeypatch.setattr(archive, 'uuid4', Identity)
    value, _ = execute(rig)
    assert (existing / 'sentinel').read_bytes() == b'keep'
    assert value['archive']['failure'] is not None and not rig[0]['processes']


def test_disabled_capture_is_explicit_without_fake_serialized_bodies(rig):
    """Host opt-out preserves planned queries and records provenance unavailability."""
    rig[1]['archive']['capturePolicy'] = 'disabled'
    value, _ = execute(rig)
    assert launcher.exitCode(value) == 0
    view = readArchive(value)
    assert view['status'] == 'sealed'
    assert not any(r['kind'] == 'request-evidence' for r in view['records'])


def test_host_v2_rejects_and_archive_expansion_preflight_has_no_io(rig):
    """Version/cap errors reject before archive creation or model transitions."""
    host = rig[1]
    host['schemaId'] = 'actant.experiment.host@2'
    with pytest.raises(schema.ValidationError):
        launcher.prepare(definition(), host)
    host['schemaId'] = 'actant.experiment.host@3'
    host['archive']['logicalRecords'] = 1
    with pytest.raises(fmt.ArchiveError):
        launcher.prepare(definition(), host)
    assert not rig[0]['processes'] and not rig[2].exists()


def test_readonly_cli_archive_selection_and_legacy_json(rig, capsys):
    """Explicit private views and read-only recovery remain separate from execution."""
    value, path = execute(rig)
    cli = importlib.import_module('first-party.applications.experimentRunner.run')
    assert cli.main(['recover', '--archive', str(archivePath(value))]) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered['status'] == 'sealed' and recovered['content'] == []
    unit = value['suite']['units'][0]['unitId']
    turn = value['suite']['units'][0]['run']['turns'][0]['turnId']
    assert cli.main(['inspect', '--archive', str(archivePath(value)), '--unit', unit, '--turn', turn, '--include-content']) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected['content'] and all(r['unitId'] == unit and r['turnId'] == turn for r in selected['records'])
    assert cli.main(['inspect', '--result', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['schemaId'] == 'actant.experiment.execution@4'


CRASH_CHILD = r'''
import importlib, json, os, sys
from pathlib import Path
from types import SimpleNamespace
a = importlib.import_module('first-party.applications.experimentRunner.archive')
s = importlib.import_module('first-party.applications.experimentRunner.definition')
r = importlib.import_module('first-party.applications.experimentRunner.results')
d = s.Snapshot(s.canonical(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))))
policy = dict(aliases={c['profile']: c['profile'] for c in d.value()['configurations']}, maxTokens=4096,
 timeoutSeconds=120, maxTurns=16, maxOutputCharacters=4096, maxConfigurations=8,
 maxRepetitions=10, maxExecutions=32, maxTurnCalls=256)
suite = r.initialSuite(d, s.unitDefinition)
caps = dict(a.fmt.DEFAULTS, partialSeconds=30)
w = a.ArchiveWriter(sys.argv[1], suite, caps, lambda: None)
run = a.ArchiveRun(w, d, SimpleNamespace(requestEvidence=None), {'limits': policy})
run.initial(suite, dict(validationPolicy=policy, capturePolicy='disabled'))
port = run.unit(suite['units'][0], 'synthetic-child-job')
t = d.value()['conversation']['turns'][0]
port.begin(t['id'], {'formatId':'actant.llm.messages@1','messages':[{'role':'user','content':t['content']}]}, {})
w.observe(SimpleNamespace(eventType='delta', text='diagnostic partial', metadata={}))
ack = None
if sys.argv[3] == 'acked':
 ack = w.checkpoint('request-evidence', dict(attemptId=port.attemptId, providerCallId='b'*32,
  phase='final', bodyRef=None, length=None, sha256=None, mayHaveSent=False,
  responseHeadersObserved=None, responseBodyObserved=None, serverConsumption='unknown'),
  unitId=port.unitId, turnId=port.turnId)
print(json.dumps({'path':str(w.path), 'ack':ack}), flush=True)
os._exit(23)
'''


@pytest.mark.parametrize('mode,sequence,partial', [('acked', 5, True), ('unacked', 3, False)])
def test_exact_model_free_child_death_preserves_only_committed_partial_prefix(tmp_path, monkeypatch, mode, sequence, partial):
    """A dedicated child exits after actual ACK; no model/native process is killed."""
    child = freshChild(monkeypatch, CRASH_CHILD, tmp_path, launcher.APP / 'example.definition.json', mode)
    assert child.returncode == 23, child.stderr
    path = json.loads(child.stdout)['path']
    view = reader.inspectArchive(path, includeContent=True)
    assert view['status'] == 'incomplete' and view['lastObservedSuiteStatus'] is None
    assert view['committedSequence'] == sequence
    assert any(r['kind'] == 'partial' for r in view['records']) is partial
    offline = freshChild(monkeypatch, OFFLINE, path)
    assert offline.returncode == 0, offline.stderr
    assert json.loads(offline.stdout)['sequence'] == sequence


def diagnosticWriter(rig, **overrides):
    """Create a raw writer stress harness without weakening launcher preflight."""
    prepared = launcher.prepare(definition(), rig[1])
    suite = launcher.results.initialSuite(prepared[0], schema.unitDefinition)
    writer = archive.ArchiveWriter(rig[2], suite, dict(fmt.DEFAULTS, **overrides), lambda: None)
    run = archive.ArchiveRun(writer, prepared[0], prepared[2], rig[1])
    run.initial(suite, launcher.provenance(rig[1]))
    return writer, run, suite


def test_timer_flush_without_new_events_and_separate_reasoning(rig):
    """An idle producer still gets timed partial commits; unknown final status stays unknown."""
    from types import SimpleNamespace
    writer, run, suite = diagnosticWriter(rig, partialSeconds=0.05)
    try:
        port = run.unit(suite['units'][0], 'diagnostic-job')
        port.begin('t1', dict(formatId='actant.llm.messages@1', messages=[dict(role='user', content='hello')]), {})
        writer.observe(SimpleNamespace(eventType='delta', text='answer', metadata={'llamaCppReasoning': {'content': 'reason'}}))
        deadline = time.monotonic() + 2
        while writer.sequence < 4 and time.monotonic() < deadline:
            writer.check()
            Event().wait(0.01)
        assert writer.sequence == 4
        view = reader.inspectArchive(writer.path, includeContent=True)
        assert view['status'] == 'incomplete'
        content = [item['value'] for item in view['content'] if type(item['value']) is dict and item['value'].get('schemaId') == fmt.PREFIX + 'partial@1']
        assert content[0]['answerDelta'] == 'answer' and content[0]['reasoningDelta'] == 'reason'
    finally:
        assert writer.close()['writerStopped']


def test_timeout_retains_worker_and_can_leave_valid_unobserved_commit(rig, monkeypatch):
    """Timeout poisons admission but never revokes or races the active disk writer."""
    writer, run, suite = diagnosticWriter(rig, ackSeconds=0.2)
    entered, release = Event(), Event()
    original = writer._commit
    def stalled(command, journal):
        """Pause after actual COMMIT but before the caller could observe ACK."""
        original(command, journal)
        if command['kind'] == 'unit-planned':
            entered.set()
            assert release.wait(5)
    monkeypatch.setattr(writer, '_commit', stalled)
    try:
        with pytest.raises(fmt.ArchiveError):
            run.unit(suite['units'][0], 'diagnostic-job')
        assert entered.is_set()
        close = writer.close()
        assert close['status'] == 'pending' and close['writerStopped'] is False
        assert close['committedSequence'] == 2 and close['ackObservation'] == 'caller-observed-through-1'
        view = reader.inspectArchive(writer.path)
        assert view['committedSequence'] == 2 and view['ackObservation'] == 'unavailable'
        with pytest.raises(fmt.ArchiveError):
            writer.checkpoint('archive-seal', {})
    finally:
        release.set()
        writer.thread.join(2)
        assert writer.close()['writerStopped']


@pytest.mark.parametrize('limit', ['pending', 'events', 'records', 'partials'])
def test_runtime_capture_bounds_poison_without_silent_truncation(rig, limit):
    """Fragmented/oversized production diagnostics hit explicit finite caps."""
    from types import SimpleNamespace
    caps = dict(partialEvents=1, partialSeconds=0.05)
    if limit == 'pending':
        caps['pendingBytes'] = 256
    elif limit == 'events':
        caps['turnEvents'] = 1
    elif limit == 'records':
        caps['logicalRecords'] = 3
    else:
        caps['partialRecords'] = 1
    writer, run, suite = diagnosticWriter(rig, **caps)
    try:
        port = run.unit(suite['units'][0], 'diagnostic-job')
        port.begin('t1', dict(formatId='actant.llm.messages@1', messages=[dict(role='user', content='hello')]), {})
        writer.observe(SimpleNamespace(eventType='delta', text='x' * (500 if limit == 'pending' else 1), metadata={}))
        if limit in ('events', 'partials'):
            deadline = time.monotonic() + 2
            while limit == 'partials' and writer.sequence < 4 and time.monotonic() < deadline:
                Event().wait(0.01)
            writer.observe(SimpleNamespace(eventType='delta', text='y', metadata={}))
        deadline = time.monotonic() + 2
        while writer.failure is None and time.monotonic() < deadline:
            Event().wait(0.01)
        with pytest.raises(fmt.ArchiveError):
            writer.check()
        assert reader.inspectArchive(writer.path)['status'] == 'incomplete'
    finally:
        writer.close()


def test_idle_stream_timer_failure_cancels_exact_job_without_new_delta(rig, monkeypatch):
    """Background persistence failure reaches cancellation even while producer is idle."""
    released = Event()
    rig[1]['archive']['partialSeconds'] = 0.05
    originalCommit, originalCancel = archive.ArchiveWriter._commit, launcher.SuiteControl.cancel
    def cancel(control):
        """Deliver normal exact-Job cancellation before unblocking synthetic reader."""
        originalCancel(control)
        released.set()
    def fail(writer, command, journal):
        """Fail only the timer's partial commit, without any additional stream event."""
        if command['kind'] == 'partial':
            raise OSError(SECRET)
        return originalCommit(writer, command, journal)
    def lines():
        """Pause after first content until the background fatal channel signals."""
        from tests.first_party.experimentRunner.test_experimentRunner import stream
        yield stream('idle partial')[0]
        assert released.wait(3)
        yield from stream('later content')
    rig[0]['streams'][0] = lines()
    monkeypatch.setattr(archive.ArchiveWriter, '_commit', fail)
    monkeypatch.setattr(launcher.SuiteControl, 'cancel', cancel)
    value, _ = execute(rig)
    assert rig[0]['calls'] == 1 and value['suite']['units'][0]['jobOutcome'] == 'Cancelled'
    assert value['archive']['failure'] is not None and readArchive(value)['status'] == 'incomplete'
    assert SECRET not in repr(value)


def test_late_required_final_capture_failure_keeps_original_turn_cleanup(rig, monkeypatch):
    """Failure after native cleanup cannot erase the observed successful call evidence."""
    original = archive.ArchiveWriter._commit
    def fail(writer, command, journal):
        """Reject only the final evidence ACK after actual provider closeExecution."""
        if command['kind'] == 'request-evidence' and fmt.decode(command['payload'])['phase'] == 'final':
            raise OSError(SECRET)
        return original(writer, command, journal)
    monkeypatch.setattr(archive.ArchiveWriter, '_commit', fail)
    value, _ = execute(rig)
    turn = value['suite']['units'][0]['run']['turns'][0]
    assert turn['cleanup']['quiescence'] == 'confirmed' and turn['status'] == 'succeeded'
    assert turn['answer'] == ' amber ' and rig[0]['calls'] == 1
    assert value['archive']['failure'] is not None and readArchive(value)['status'] == 'incomplete'


def test_primary_interrupt_survives_archive_close_secondary(rig, monkeypatch):
    """Archive teardown cannot replace the exact primary already captured by launcher."""
    primary, secondary = KeyboardInterrupt('primary'), SystemExit('secondary')
    def lines():
        """Raise only after actual managed inference owns its stream."""
        raise primary
        yield b''
    original = archive.ArchiveWriter.close
    def close(writer):
        """Close owned handles normally then inject a secondary non-Exception."""
        original(writer)
        raise secondary
    rig[0]['streams'][0] = lines()
    monkeypatch.setattr(archive.ArchiveWriter, 'close', close)
    with pytest.raises(KeyboardInterrupt) as caught:
        execute(rig)
    assert caught.value is primary
    value = primary.experimentExecution.value()
    assert value['archive']['failure']['stage'] == 'archive-close'
    assert value['archive']['close'] is None


@pytest.mark.parametrize('mutation', ['escape', 'job-type', 'unknown-key', 'blank', 'unreferenced'])
def test_rehashed_complete_frames_still_require_typed_payloads(rig, mutation):
    """A recomputed checksum cannot turn unsupported types or paths into valid evidence."""
    value, _ = execute(rig)
    path = archivePath(value) / 'records.log'
    lines = path.read_bytes().splitlines()
    previous = fmt.digest((archivePath(value) / 'manifest.json').read_bytes())
    rebuilt = []
    for index in range(0, len(lines), 2):
        data = json.loads(lines[index])
        if index == 2:
            if mutation == 'escape':
                data['payload']['requestedOptionsRef']['sha256'] = '../outside'
            elif mutation == 'job-type':
                data['payload']['jobId'] = ['not-an-id']
            elif mutation == 'unknown-key':
                data['payload']['execute'] = True
            elif mutation == 'unreferenced':
                extra = archive.artifact(fmt.canonical({'diagnostic': 'orphan'}))
                (archivePath(value) / 'artifacts' / (extra[0]['sha256'] + '.blob')).write_bytes(extra[1])
                data['artifacts'].append(extra[0])
        data['previousCommitHash'] = previous
        data['dataSha256'] = fmt.hashWithout(data, 'dataSha256')
        commit = dict(schemaId=fmt.PREFIX + 'archive-commit@1', sequence=data['sequence'],
            previousCommitHash=previous, dataSha256=data['dataSha256'])
        commit['commitSha256'] = fmt.hashWithout(commit, 'commitSha256')
        rebuilt.extend((fmt.canonical(data) + b'\n', fmt.canonical(commit) + b'\n'))
        previous = commit['commitSha256']
    if mutation == 'blank':
        rebuilt.insert(2, b'\n')
    path.write_bytes(b''.join(rebuilt))
    view = readArchive(value)
    assert view['status'] == 'corrupt' and view['committedSequence'] == 1
    assert view['units'][0]['lastObservedStatus'] == 'not-started'


def test_selected_inspection_bound_rejects_instead_of_fabricating_truncation(rig):
    """Small view caps do not change archived evidence or relabel it as valid truncated data."""
    rig[1]['archive']['inspectBytes'] = 2000
    value, _ = execute(rig)
    assert launcher.exitCode(value) == 0
    with pytest.raises(fmt.ArchiveError):
        readArchive(value, includeContent=True)


def test_manifest_only_is_incomplete_and_filesystem_reparse_is_rejected(rig, monkeypatch):
    """An empty journal has no invented outcomes; simulated reparse flags deny reads."""
    from types import SimpleNamespace
    prepared = launcher.prepare(definition(), rig[1])
    suite = launcher.results.initialSuite(prepared[0], schema.unitDefinition)
    writer = archive.ArchiveWriter(rig[2], suite, fmt.DEFAULTS, lambda: None)
    writer.start()
    deadline = time.monotonic() + 2
    while not (writer.path / 'records.log').exists() and time.monotonic() < deadline:
        Event().wait(0.01)
    assert writer.close()['writerStopped']
    view = reader.inspectArchive(writer.path)
    assert view['status'] == 'incomplete' and view['committedSequence'] == 0
    original = Path.lstat
    def reparse(path):
        """Model Windows reparse metadata on an actual selected archive boundary."""
        result = original(path)
        if path == writer.path / 'artifacts':
            return SimpleNamespace(st_mode=result.st_mode, st_size=result.st_size, st_file_attributes=1024)
        return result
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(fmt.ArchiveError):
        reader.inspectArchive(writer.path)


def test_stalled_required_sink_observes_cancellation_without_blocking_relay(rig, monkeypatch):
    """A waiting pre-send sink cannot hold queue/stop locks or freeze exact-Job cancellation."""
    entered, released = Event(), Event()
    writers = []
    rig[1]['archive']['ackSeconds'] = 0.2
    original = archive.ArchiveWriter._commit
    def stalled(writer, command, journal):
        """Stall only the first serialized receipt before its actual disk commit."""
        if command['kind'] == 'request-evidence' and fmt.decode(command['payload'])['phase'] == 'serialized':
            writers.append(writer)
            entered.set()
            assert released.wait(5)
        return original(writer, command, journal)
    monkeypatch.setattr(archive.ArchiveWriter, '_commit', stalled)
    control = launcher.SuiteControl()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(execute, rig, control=control)
        try:
            assert entered.wait(3)
            started = time.monotonic()
            control.cancel()
            assert time.monotonic() - started < 1
            value, _ = pending.result(timeout=3)
            assert rig[0]['calls'] == 0 and value['suite']['units'][0]['jobOutcome'] == 'Cancelled'
            assert value['archive']['close']['status'] == 'pending'
        finally:
            released.set()
            for writer in writers:
                writer.thread.join(2)
                assert not writer.thread.is_alive()


def test_asset_provenance_hashes_once_and_persists_actual_launch_evidence(rig):
    """Supplied paths are distinct from content hashes and later actual launch evidence."""
    rig[1]['archive']['hashAssets'] = True
    value, _ = execute(rig)
    view = readArchive(value, includeContent=True)
    contents = [item['value'] for item in view['content'] if type(item['value']) is dict]
    facts = next(item['facts'] for item in contents if item.get('schemaId') == fmt.PREFIX + 'provenance@1')
    assert len(facts['assets']) == 2
    assert all(asset['status'] == 'hash-observed' and asset['sha256'] == fmt.digest(b'') for asset in facts['assets'])
    assert all(source['versionHeader'].startswith('# file: ') and len(source['sha256']) == 64 for source in facts['sources'])
    unit = next(item for item in contents if item.get('schemaId') == fmt.PREFIX + 'unit-summary@1')
    assert unit['configurationEvidence'] == value['suite']['units'][0]['configurationEvidence']


def test_eight_call_synthetic_archive_order_and_fresh_offline_reopen(rig, monkeypatch):
    """The complete 2x2x2 archive matches independent synthetic transport observations."""
    value, _ = executeSuite(rig, suiteDefinition(rig))
    assert launcher.exitCode(value) == 0
    view = readArchive(value)
    serialized = [record for record in view['records'] if record['kind'] == 'request-evidence'
        and record['payload']['phase'] == 'serialized']
    blobs = reader.BlobReader(archivePath(value), fmt.DEFAULTS)
    archived = [blobs(record['payload']['bodyRef']) for record in serialized]
    wire = [body for url, body in rig[0]['requestBytes'] if url.endswith('/completions')]
    assert archived == wire and len(wire) == 8
    assert len([r for r in view['records'] if r['kind'] == 'unit-outcome']) == 4
    assert len([r for r in view['records'] if r['kind'] == 'turn-completed']) == 8
    assert [len(json.loads(body)['messages']) for body in archived] == [1, 3] * 4
    assert len(rig[0]['processes']) == 2 and all(p.returncode == 0 for p in rig[0]['processes'])
    for asset in {rig[1]['llamaCpp']['executable'], *(m['modelPath'] for m in rig[1]['llamaCpp']['models'].values())}:
        Path(asset).unlink()
    child = freshChild(monkeypatch, OFFLINE, archivePath(value))
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)['status'] == 'sealed'


def test_startup_credentials_and_probe_text_are_not_archived(rig):
    """Full provider configuration accessor is projected before private persistence."""
    rig[1]['llamaCpp']['extraArgs'] = ['--api-key', SECRET]
    value, path = execute(rig)
    assert launcher.exitCode(value) == 0
    assert any(SECRET in p.args for p in rig[0]['processes'])
    assert SECRET not in path.read_text(encoding='utf-8')
    for item in archivePath(value).rglob('*'):
        if item.is_file():
            assert SECRET.encode() not in item.read_bytes()
    evidence = value['suite']['units'][0]['configurationEvidence']
    assert evidence['rawLaunchInputs'] == 'omitted-private-policy'
    assert evidence['launch']['actualArgvObserved'] is True


def test_background_primary_interrupt_survives_secondary_archive_close(rig, monkeypatch):
    """A worker's first non-Exception exits through the host latch, never observerErrors."""
    primary, secondary = KeyboardInterrupt('worker primary'), SystemExit('close secondary')
    originalCommit, originalClose = archive.ArchiveWriter._commit, archive.ArchiveWriter.close
    def commit(writer, command, journal):
        """Raise the exact primary in the actual background partial persistence path."""
        if command['kind'] == 'partial':
            raise primary
        return originalCommit(writer, command, journal)
    def close(writer):
        """Finish owned worker disposal, then inject independent close interruption."""
        originalClose(writer)
        raise secondary
    monkeypatch.setattr(archive.ArchiveWriter, '_commit', commit)
    monkeypatch.setattr(archive.ArchiveWriter, 'close', close)
    with pytest.raises(KeyboardInterrupt) as caught:
        execute(rig)
    assert caught.value is primary
    value = primary.experimentExecution.value()
    assert value['archive']['failure'] is not None and rig[0]['calls'] == 1
