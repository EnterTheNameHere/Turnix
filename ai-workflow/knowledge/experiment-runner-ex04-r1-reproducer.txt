# file: tests/first_party/experimentRunner/test_reviewArchive.py ; version: 1
"""Independent exact-provenance and typed recovery checks for delivered archives."""
import json
import pytest
from tests.first_party.experimentRunner.test_archive import rig, execute, archivePath, reader, fmt


def rewrite(path, records):
    """Rehash an intentionally inconsistent record stream to test semantic validation."""
    previous = fmt.digest((path / 'manifest.json').read_bytes())
    lines = []
    terminalSequence = None
    for sequence, record in enumerate(records, 1):
        if record['kind'] == 'suite-outcome':
            terminalSequence = sequence
        if record['kind'] == 'archive-seal':
            record['payload']['terminalSequence'] = terminalSequence
        record['sequence'], record['previousCommitHash'] = sequence, previous
        record['dataSha256'] = fmt.hashWithout(record, 'dataSha256')
        commit = dict(schemaId=fmt.PREFIX + 'archive-commit@1', sequence=sequence,
            dataSha256=record['dataSha256'], previousCommitHash=previous)
        commit['commitSha256'] = fmt.hashWithout(commit, 'commitSha256')
        lines.extend([fmt.canonical(record) + b'\n', fmt.canonical(commit) + b'\n'])
        previous = commit['commitSha256']
    (path / 'records.log').write_bytes(b''.join(lines))


@pytest.mark.parametrize('mode', ['missing-body-predecessors', 'not-sent'])
def test_required_success_without_serialized_request_rejects(rig, mode):
    """Required provenance cannot be complete when a final record lacks its body predecessor."""
    value, _ = execute(rig)
    path = archivePath(value)
    records = [json.loads(line) for line in (path / 'records.log').read_bytes().splitlines()[::2]]
    if mode == 'missing-body-predecessors':
        records = [record for record in records if not (record['kind'] == 'request-evidence'
            and record['payload']['phase'] != 'final')]
    else:
        for record in records:
            if record['kind'] == 'request-evidence' and record['payload']['phase'] == 'final':
                record['payload'].update(mayHaveSent=False, responseHeadersObserved=None, responseBodyObserved=None)
    rewrite(path, records)
    view = reader.inspectArchive(path)
    assert view['status'] == 'corrupt', (view['status'], view['lastObservedSuiteStatus'])
