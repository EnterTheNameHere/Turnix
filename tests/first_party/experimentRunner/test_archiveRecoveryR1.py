# file: tests/first_party/experimentRunner/test_archiveRecoveryR1.py ; version: 1
"""Per-attempt required provenance and truthful failure-only archive semantics."""
from types import SimpleNamespace
import json

import pytest

from tests.first_party.experimentRunner.test_archive import (
    rig, execute, archivePath, reader, fmt, archive, launcher, diagnosticWriter,
)
from tests.first_party.experimentRunner.test_reviewArchive import rewrite


def recordsAt(path):
    """Read only committed DATA records from a known successful test archive."""
    return [json.loads(line) for line in (path / 'records.log').read_bytes().splitlines()[::2]]


def emit(port, phase, **facts):
    """Feed modeled private provider observations through the actual typed writer port."""
    eventType = launcher.loadAdapter()._evidence.RequestEvidence
    return port.evidence(eventType('b' * 32, ('synthetic',), phase, **facts))


def serialized(port):
    """Acknowledge exact bounded synthetic body bytes, not an actual provider run."""
    body = b'{"synthetic":"body"}'
    emit(port, 'serialized', body=body, length=len(body), sha256=fmt.digest(body))


def begin(rig):
    """Prepare one raw diagnostic attempt without executing any native or HTTP operation."""
    writer, run, suite = diagnosticWriter(rig)
    port = run.unit(suite['units'][0], 'diagnostic-job')
    port.begin('t1', dict(formatId='actant.llm.messages@1', messages=[dict(role='user', content='hello')]), {})
    return writer, port, suite


def turnResult(suite, *, succeeded=False):
    """Create a complete typed outcome to isolate evidence validation from other fields."""
    result = dict(suite['units'][0]['run']['turns'][0])
    result.update(status='succeeded' if succeeded else 'failed', startedTimeNs=1,
        endedTimeNs=2, durationNs=1, answer='answer' if succeeded else None)
    return result


@pytest.mark.parametrize('mode', ['serialized', 'transport-attempt', 'response-headers-observed', 'final',
    'no-evidence', 'not-sent-without-headers', 'body-unavailable', 'headers-unavailable', 'false-with-response'])
def test_missing_or_contradictory_required_success_is_corrupt_with_selected_prefix(rig, mode):
    """Rebuilt valid checksums cannot substitute for required successful provenance."""
    value, _ = execute(rig)
    path = archivePath(value)
    records = recordsAt(path)
    unitId = value['suite']['units'][0]['unitId']
    first = value['suite']['units'][0]['run']['turns'][0]['turnId']
    changed = []
    for record in records:
        selected = record['unitId'] == unitId and record['turnId'] == first
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
    rewrite(path, changed)
    view = reader.inspectArchive(path)
    selected = reader.inspectArchive(path, unitId=unitId, turnId=first)
    assert view['status'] == selected['status'] == 'corrupt'
    assert view['failureSequence'] == selected['failureSequence'] == view['committedSequence'] + 1
    assert view['committedSequence'] == selected['committedSequence']
    assert view['lastObservedSuiteStatus'] is None
    turn = selected['units'][0]['turns'][0]
    assert turn['lastObservedStatus'] == 'planned'
    if changed[view['failureSequence'] - 1]['kind'] == 'request-evidence':
        assert turn['capturePhase'] != 'final'


@pytest.mark.parametrize('flow', ['before-serialization', 'before-attempt', 'pre-connect', 'pre-header',
    'after-headers', 'partial-before-final', 'partial-after-final', 'no-provider-record'])
def test_truthful_failure_diagnostics_remain_valid_for_writer_and_reader(rig, flow):
    """Failed/incomplete turns retain genuine not-sent and ambiguous-send observations."""
    writer, port, suite = begin(rig)
    try:
        if flow not in ('before-serialization', 'no-provider-record'):
            serialized(port)
        if flow not in ('before-serialization', 'before-attempt', 'no-provider-record'):
            emit(port, 'transport-attempt')
        headers = flow in ('after-headers', 'partial-before-final', 'partial-after-final')
        if headers:
            emit(port, 'response-headers-observed', responseHeadersObserved=True)
        if flow == 'partial-before-final':
            writer.observe(SimpleNamespace(eventType='delta', text='partial', metadata={}))
        if flow != 'no-provider-record':
            emit(port, 'final', mayHaveSent=flow not in ('before-serialization', 'before-attempt', 'pre-connect'),
                responseHeadersObserved=True if headers else None,
                responseBodyObserved=True if flow.startswith('partial-') else None)
        if flow == 'partial-after-final':
            writer.observe(SimpleNamespace(eventType='delta', text='partial', metadata={}))
        port.completed(turnResult(suite))
        writer.check()
        view = reader.inspectArchive(writer.path)
        assert view['status'] == 'incomplete' and view['lastObservedSuiteStatus'] is None
        turn = view['units'][0]['turns'][0]
        assert turn['lastObservedStatus'] == 'failed'
        assert turn['cleanup']['quiescence'] == 'unknown'
        assert turn['committedAnswerCharacters'] == (7 if flow.startswith('partial-') else 0)
        finals = [r['payload'] for r in view['records'] if r['kind'] == 'request-evidence' and r['payload']['phase'] == 'final']
        if finals:
            assert finals[0]['serverConsumption'] == 'unknown'
        assert not rig[0]['processes'] and not rig[0]['requestBytes']
    finally:
        assert writer.close()['writerStopped']


@pytest.mark.parametrize('mode', ['sent-without-attempt', 'header-downgrade', 'partial-downgrade', 'success-without-body'])
def test_writer_rejects_invalid_candidate_before_commit_and_retains_prefix(rig, mode):
    """Live writer and recovery use the same history checks; invalid candidates never publish."""
    writer, port, suite = begin(rig)
    try:
        if mode in ('header-downgrade', 'partial-downgrade'):
            serialized(port)
            emit(port, 'transport-attempt')
            emit(port, 'response-headers-observed', responseHeadersObserved=True)
        if mode == 'partial-downgrade':
            writer.observe(SimpleNamespace(eventType='delta', text='partial', metadata={}))
        with pytest.raises(fmt.ArchiveError):
            if mode == 'success-without-body':
                port.completed(turnResult(suite, succeeded=True))
            else:
                emit(port, 'final', mayHaveSent=True,
                    responseHeadersObserved=True if mode == 'partial-downgrade' else None,
                    responseBodyObserved=None)
        view = reader.inspectArchive(writer.path)
        assert view['status'] == 'incomplete' and view['lastObservedSuiteStatus'] is None
        assert view['units'][0]['turns'][0]['lastObservedStatus'] == 'planned'
        assert not any(r['kind'] == 'turn-completed' or (r['kind'] == 'request-evidence' and r['payload']['phase'] == 'final') for r in view['records'])
        if mode == 'partial-downgrade':
            assert view['units'][0]['turns'][0]['committedAnswerCharacters'] == 7
        with pytest.raises(fmt.ArchiveError):
            writer.check()
    finally:
        assert writer.close()['writerStopped']


@pytest.mark.parametrize('required', [True, False])
def test_empty_success_and_explicit_opt_out_preserve_normal_managed_behavior(rig, required):
    """Zero answer deltas still require observed response bytes under required policy."""
    from tests.first_party.experimentRunner.test_experimentRunner import stream
    rig[1]['archive']['capturePolicy'] = 'required' if required else 'disabled'
    rig[0]['streams'] = [stream('', ''), stream('', '')]
    value, _ = execute(rig)
    assert launcher.exitCode(value) == 0
    view = reader.inspectArchive(archivePath(value))
    assert view['status'] == 'sealed' and view['lastObservedSuiteStatus'] == 'succeeded'
    assert not any(r['kind'] == 'partial' for r in view['records'])
    finals = [r['payload'] for r in view['records'] if r['kind'] == 'request-evidence' and r['payload']['phase'] == 'final']
    assert len(finals) == (2 if required else 0)
    assert all(r['mayHaveSent'] is True and r['responseHeadersObserved'] is True and r['responseBodyObserved'] is True for r in finals)


@pytest.mark.parametrize('policy', [None, False, 'optional'])
def test_invalid_policy_cannot_silently_become_capture_opt_out(rig, policy):
    """An unsupported/missing policy is corruption, not an implicit disabled policy."""
    value, _ = execute(rig)
    path = archivePath(value)
    records = recordsAt(path)
    initial = records[0]
    oldRef = initial['payload']['initialProvenanceRef']
    provenance = fmt.decode(reader.BlobReader(path, fmt.DEFAULTS)(oldRef))
    if policy is None:
        provenance['facts'].pop('capturePolicy')
    else:
        provenance['facts']['capturePolicy'] = policy
    replacement = archive.artifact(fmt.canonical(provenance))
    (path / 'artifacts' / (replacement[0]['sha256'] + '.blob')).write_bytes(replacement[1])
    initial['payload']['initialProvenanceRef'] = replacement[0]
    initial['artifacts'] = [replacement[0] if ref == oldRef else ref for ref in initial['artifacts']]
    rewrite(path, records)
    view = reader.inspectArchive(path)
    assert view['status'] == 'corrupt' and view['committedSequence'] == 0
    assert view['units'] == []


@pytest.mark.parametrize('flow', ['before-serialization', 'pre-connect', 'pre-header', 'partial'])
def test_actual_managed_failure_archives_remain_sealed_failed_without_repair(rig, monkeypatch, flow):
    """Normal emitted failed-call histories, including final-only, remain recoverable."""
    adapter = launcher.loadAdapter()
    if flow == 'before-serialization':
        original = adapter._Session.messages
        def messages(session, query):
            """Fail after scope claim but before serialization, preserving normal cleanup."""
            if session.capture is not None:
                raise adapter.LlmProviderProtocolError('Synthetic serialization preparation failure.')
            return original(session, query)
        monkeypatch.setattr(adapter._Session, 'messages', messages)
    elif flow in ('pre-connect', 'pre-header'):
        originalOpen = adapter._transport.OwnedHttpTransport.open
        def openRequest(transport, request, **kwargs):
            """Fail only synthetic inference IO with the exact selected send ambiguity."""
            if request.full_url.endswith('/completions'):
                transport.mayHaveSent = flow == 'pre-header'
                raise OSError('Synthetic connection failure.')
            return originalOpen(transport, request, **kwargs)
        monkeypatch.setattr(adapter._transport.OwnedHttpTransport, 'open', openRequest)
    else:
        from tests.first_party.experimentRunner.test_experimentRunner import stream
        def lines():
            """Deliver one real parser delta then fail the synthetic response reader."""
            yield stream('partial')[0]
            raise OSError('Synthetic stream failure.')
        rig[0]['streams'][0] = lines()
    value, _ = execute(rig)
    assert launcher.exitCode(value) == 1 and value['suite']['units'][0]['jobOutcome'] == 'Failed'
    assert value['archive']['failure'] is None
    view = reader.inspectArchive(archivePath(value))
    assert view['status'] == 'sealed' and view['lastObservedSuiteStatus'] == 'failed'
    requests = [r['payload'] for r in view['records'] if r['kind'] == 'request-evidence']
    assert [r['phase'] for r in requests] == (['final'] if flow == 'before-serialization' else
        ['serialized', 'transport-attempt', 'response-headers-observed', 'final'] if flow == 'partial' else
        ['serialized', 'transport-attempt', 'final'])
    assert requests[-1]['mayHaveSent'] is (flow in ('pre-header', 'partial'))
    turn = view['units'][0]['turns'][0]
    assert turn['lastObservedStatus'] == 'failed'
    assert turn['cleanup']['quiescence'] == ('confirmed' if flow in ('before-serialization', 'pre-connect') else 'unknown')
    assert all(record['serverConsumption'] == 'unknown' for record in requests)
