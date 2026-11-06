# file: tests/first_party/experimentRunner/test_ex05R1.py ; version: 1
"""R1 cleanup fidelity and temporal validation over real PM codec values and managed archives."""
import csv
import io
import json

import pytest

from backend.llm.streamObservation import StreamObservation, StreamObservationConfiguration, StreamOutcome
from backend.observation import ObservationRequest, ObservationLifetime, ObservationSnapshot, ObservationPublicationSnapshot, correlate
from tests.first_party.experimentRunner.test_archive import archivePath, readArchive
from tests.first_party.experimentRunner.test_reporting import rig, execute, reporting, exporting
from tests.first_party.experimentRunner.test_observations import host4, rewriteV2, ofmt, observations


class Clock:
    """Deterministic same-epoch PM clock with explicit optional read failure."""

    def __init__(self):
        """Start at a nonzero nanosecond origin without hardware or wall-clock attribution."""
        self.now = 1_000_000_000
        self.failed = False

    def read(self):
        """Return known monotonic evidence or an unavailable-clock diagnostic."""
        if self.failed:
            raise OSError('test-clock-unavailable')
        return self.now


def publicStream(scenario):
    """Serialize actual PM handles via the unchanged public codec; no publication/grant is minted."""
    clock = Clock()
    if scenario == 'large-epoch':
        clock.now = 2 ** 70 + 123
    stream = StreamObservation(StreamObservationConfiguration(windowSeconds=1, historyWindows=2), monotonicNs=clock.read)
    if scenario == 'released-before-start':
        stream.release()
    elif scenario != 'not-started':
        clock.failed = scenario == 'unavailable-before-origin'
        claim = stream.begin()
        if scenario != 'unavailable-before-origin':
            if scenario not in ('active-empty-zero', 'zero-elapsed-text', 'same-time-deliveries'):
                clock.now += 250_000_000
            if scenario not in ('active-empty-zero', 'active-empty', 'terminal-empty'):
                claim.recordText('é🙂')
            if scenario in ('truncated', 'cancelled', 'failed', 'closed'):
                clock.now += 1_500_000_000
                claim.recordText('more')
            if scenario == 'truncated':
                clock.now += 4_000_000_000
                claim.recordText('last')
            if scenario == 'same-time-deliveries':
                claim.recordText('more')
            if scenario in ('partial-text', 'active-empty', 'terminal-empty'):
                clock.now += 500_000_000
        if scenario.startswith('unavailable'):
            clock.failed = True
        if scenario == 'released-after-text':
            stream.release()
        elif scenario not in ('active-empty-zero', 'active-empty', 'partial-text'):
            claim.finish({'cancelled': StreamOutcome.CANCELLED, 'failed': StreamOutcome.FAILED,
                          'closed': StreamOutcome.CLOSED}.get(scenario, StreamOutcome.COMPLETED))
    snapshot = stream.snapshot()
    observation = ObservationSnapshot((), snapshot, correlate(snapshot.windows, ()), False, None,
        'optional-snapshot-failed' if scenario == 'outer-unavailable' else None, snapshot.released, not snapshot.active)
    publication = ObservationPublicationSnapshot('1' * 32, '2' * 32, ObservationLifetime.RUN,
        ObservationRequest(memory=False, cpu=False, gpu=False, stream=True, historySamples=2, historyWindows=2), observation)
    result = ofmt.fmt.decode(observations.codec.serialize(publication, 'codec-test-only', 0))
    del result['publicationId'], result['revision']
    return result


@pytest.mark.parametrize('scenario', ['not-started', 'active-empty-zero', 'active-empty', 'terminal-empty', 'partial-text',
    'zero-elapsed-text', 'same-time-deliveries', 'cancelled', 'failed', 'closed', 'truncated',
    'unavailable-before-origin', 'unavailable-after-text', 'released-before-start', 'released-after-text', 'large-epoch', 'outer-unavailable'])
def test_actual_pm_missing_empty_partial_unavailable_and_truncated_evidence_stays_valid(scenario):
    """Stricter archive validation accepts real nullable, lifecycle and retention semantics."""
    value = publicStream(scenario)
    ofmt.publicData(value)
    if scenario == 'truncated':
        assert int(value['stream']['droppedWindowCount']) > 0
        assert sum(int(w['characterCount']) for w in value['stream']['windows']) < int(value['stream']['characterCount'])
        # First gap in a retained window may originate before that window.
        window = next(w for w in value['stream']['windows'] if w['deliveryCount'] != '0')
        assert int(window['maxGapNs']) > int(window['endNs']) - int(window['startNs'])
    if scenario.startswith('unavailable'):
        assert value['stream']['availability'] == 'unavailable'


@pytest.mark.parametrize('status', ['attempted', 'acknowledged', 'failed', 'unknown', 'unavailable', None])
@pytest.mark.parametrize('quiescence', ['unknown', 'confirmed'])
def test_cleanup_action_and_quiescence_survive_report_json_csv_table_and_export(rig, tmp_path, status, quiescence):
    """Every actual action status remains independent from confirmed versus unknown quiescence."""
    value, _ = execute(rig)
    source = archivePath(value)
    report = reporting.report(source)
    report['rows'][0]['callCleanup'] = reporting.cleanCleanup(dict(status=status, quiescence=quiescence))
    expected = dict(status='unavailable' if status is None else status, quiescence=quiescence)
    reporting.reportSchema.validate(report)
    assert report['rows'][0]['callCleanup'] == expected
    assert json.loads(exporting.jsonBytes(report))['rows'][0]['callCleanup'] == expected
    row = list(csv.DictReader(io.StringIO(exporting.csvBytes(report).decode())))[0]
    assert (row['cleanupStatus'], row['cleanupQuiescence']) == (expected['status'], expected['quiescence'])
    assert json.loads(exporting.table(report).splitlines()[1])['callCleanup'] == expected
    exporting.publish(report, tmp_path, 'summary', kind='report', format='json', sourceDirectories=[source])
    exported = json.loads((tmp_path / 'summary' / 'report.json').read_bytes())
    assert exported['rows'][0]['callCleanup'] == expected


def mutateClock(value, fault):
    """Alter one typed same-epoch fact without changing schema, clock identity or data types."""
    stream = value['stream']
    window = stream['windows'][-1]
    if fault == 'latency-null':
        stream['firstTextLatencyNs'] = None
    elif fault == 'latency':
        stream['firstTextLatencyNs'] = str(int(stream['firstTextLatencyNs']) + 1)
    elif fault == 'through':
        stream['observedThroughNs'] = str(int(stream['originNs']) - 1)
    elif fault == 'end':
        stream['endNs'] = str(int(stream['observedThroughNs']) + 1)
    elif fault == 'silence':
        stream['silenceNs'] = str(int(stream['silenceNs']) + 1)
    elif fault == 'first-last':
        stream['lastTextNs'] = str(int(stream['firstTextNs']) - 1)
    elif fault == 'first-without-delivery':
        stream['characterCount'] = stream['deliveryCount'] = '0'
    elif fault == 'active-terminal':
        stream['active'] = True
    elif fault == 'available-unavailable-state':
        stream['state'] = 'unavailable'
    elif fault == 'unavailable-active-state':
        stream['availability'] = 'unavailable'
    elif fault == 'window-boundary':
        window['endNs'] = str(int(stream['observedThroughNs']) + 1)
    elif fault == 'window-delivery-time':
        window['firstTextNs'] = str(int(window['startNs']) - 1)
    elif fault == 'window-gaps':
        window['gapCount'] = str(int(window['deliveryCount']) + 1)
    elif fault == 'window-rate':
        window['charactersPerSecond']['value'] += 1
    elif fault == 'window-total':
        stream['characterCount'] = str(int(stream['characterCount']) + 1)
    elif fault == 'correlation-window':
        value['correlation'][-1]['endNs'] = str(int(window['endNs']) + 1)
    elif fault == 'correlation-gap':
        value['correlation'][-1]['uncoveredIntervalsNs'] = [[window['startNs'], str(int(window['endNs']) + 1)]]
    else:
        raise AssertionError('Unknown test fault.')


FAULTS = ('latency-null', 'latency', 'through', 'end', 'silence', 'first-last', 'first-without-delivery', 'active-terminal',
          'available-unavailable-state', 'unavailable-active-state', 'window-boundary', 'window-delivery-time', 'window-gaps',
          'window-rate', 'window-total', 'correlation-window', 'correlation-gap')


@pytest.mark.parametrize('fault', FAULTS)
def test_rehashed_clock_fault_is_corrupt_before_metrics_with_matching_selected_prefix(rig, fault):
    """Rehashed false temporal facts never become valid retained metrics or full-success reports."""
    host4(rig)
    value, _ = execute(rig)
    path = archivePath(value)
    original = readArchive(value)
    records = [json.loads(line) for line in (path / 'records.log').read_bytes().splitlines()[::2]]
    record = next(r for r in records if r['kind'] == 'observation-snapshot')
    ref = record['payload']['observationRef']
    document = ofmt.fmt.decode((path / 'artifacts' / (ref['sha256'] + '.blob')).read_bytes())
    mutateClock(document['publicData'], fault)
    with pytest.raises(ofmt.fmt.ArchiveError):
        ofmt.observation(document)
    raw = ofmt.fmt.canonical(document)
    ref.update(bytes=len(raw), sha256=ofmt.fmt.digest(raw))
    record['artifacts'] = [ref]
    (path / 'artifacts' / (ref['sha256'] + '.blob')).write_bytes(raw)
    rewriteV2(path, records)
    view = readArchive(value)
    selected = readArchive(value, unitId=record['unitId'], turnId=record['turnId'])
    assert view['status'] == selected['status'] == 'corrupt'
    assert view['committedSequence'] == selected['committedSequence'] == record['sequence'] - 1
    assert view['records'] == original['records'][:view['committedSequence']]
    assert view['failureSequence'] == record['sequence'] and view['lastObservedSuiteStatus'] is None
    report = reporting.report(path)
    reporting.reportSchema.validate(report)
    assert report['scopeCompleteness'] == 'invalid-prefix'
    assert all(m['value'] is None for row in report['rows'] for m in row['metrics'] if m['metricId'].startswith('pm.'))


def test_available_pm_values_are_rejected_before_online_archive_acceptance(rig, monkeypatch):
    """Invalid optional codec output becomes unavailable once, without failing or retrying inference."""
    host4(rig)
    original = observations.codec.serialize
    def inconsistent(*args):
        """Inject only an otherwise valid typed latency contradiction into the public copy."""
        data = ofmt.fmt.decode(original(*args))
        if data['stream']['firstTextLatencyNs'] is not None:
            mutateClock(data, 'latency')
        return ofmt.fmt.canonical(data)
    monkeypatch.setattr(observations.codec, 'serialize', inconsistent)
    value, _ = execute(rig)
    view = readArchive(value, includeContent=True)
    assert value['suite']['status'] == 'succeeded' and view['status'] == 'sealed' and rig[0]['calls'] == 2
    snapshot = next(c['value'] for c in view['content'] if type(c['value']) is dict and
                    c['value'].get('schemaId') == ofmt.fmt.PREFIX + 'observation@1')
    assert snapshot['availability'] == 'unavailable' and snapshot['publicData'] is None


def test_outer_stream_unavailability_is_preserved_without_erasing_valid_snapshot(rig, monkeypatch):
    """A retained optional facade failure excludes stream metrics while keeping its valid numeric evidence."""
    host4(rig)
    original = observations.codec.serialize
    def unavailable(*args):
        """Model a prior optional snapshot failure alongside a now-readable underlying stream."""
        data = ofmt.fmt.decode(original(*args))
        data['streamAvailability'] = 'unavailable'
        return ofmt.fmt.canonical(data)
    monkeypatch.setattr(observations.codec, 'serialize', unavailable)
    value, _ = execute(rig)
    report = reporting.report(archivePath(value))
    assert readArchive(value)['status'] == 'sealed' and value['suite']['status'] == 'succeeded'
    assert all(m['value'] is None and m['reason'] == 'observation-unavailable'
               for row in report['rows'] for m in row['metrics'] if m['metricId'].startswith('pm.'))
