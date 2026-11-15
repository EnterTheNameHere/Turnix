# file: tests/first_party/experimentRunner/test_reviewEx05.py ; version: 1
"""Independent EX-05 retained cleanup and monotonic evidence integrity regressions."""
import json
import pytest
from tests.first_party.experimentRunner.test_reporting import rig, execute, reporting, exporting
from tests.first_party.experimentRunner.test_archive import archivePath, readArchive
from tests.first_party.experimentRunner.test_observations import host4, rewriteV2, ofmt


def test_report_preserves_acknowledged_provider_cleanup(rig):
    """A successful actual provider acknowledgement cannot become unknown in a report."""
    value, _ = execute(rig)
    original = value['suite']['units'][0]['run']['turns'][0]['cleanup']
    assert original['status'] == 'acknowledged' and original['quiescence'] == 'confirmed'
    report = reporting.report(archivePath(value))
    assert report['rows'][0]['callCleanup'] == {k: original[k] for k in ('status', 'quiescence')}
    assert b'acknowledged' in exporting.csvBytes(report)


@pytest.mark.parametrize('fault', ['derived-latency', 'backwards-observed-boundary'])
def test_archive2_rejects_contradictory_retained_clock_metrics(rig, fault):
    """Checksummed typed PM records must respect their own same-epoch monotonic facts."""
    host4(rig)
    value, _ = execute(rig)
    path = archivePath(value)
    records = [json.loads(line) for line in (path / 'records.log').read_bytes().splitlines()[::2]]
    record = next(r for r in records if r['kind'] == 'observation-snapshot')
    ref = record['payload']['observationRef']
    document = json.loads((path / 'artifacts' / (ref['sha256'] + '.blob')).read_bytes())
    stream = document['publicData']['stream']
    assert stream['firstTextLatencyNs'] == str(int(stream['firstTextNs']) - int(stream['originNs']))
    if fault == 'derived-latency':
        stream['firstTextLatencyNs'] = str(int(stream['firstTextLatencyNs']) + 1)
    else:
        stream['observedThroughNs'] = str(int(stream['originNs']) - 1)
    raw = ofmt.fmt.canonical(document)
    ref.update(bytes=len(raw), sha256=ofmt.fmt.digest(raw))
    record['artifacts'] = [ref]
    (path / 'artifacts' / (ref['sha256'] + '.blob')).write_bytes(raw)
    rewriteV2(path, records)
    view = readArchive(value)
    assert view['status'] == 'corrupt', (view['status'], view['lastObservedSuiteStatus'])
    assert view['failureSequence'] == view['committedSequence'] + 1
