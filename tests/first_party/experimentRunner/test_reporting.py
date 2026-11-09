# file: tests/first_party/experimentRunner/test_reporting.py ; version: 2
"""Actual managed archives, offline field comparisons, presentation and publication faults."""
import importlib
import json

import pytest

from tests.first_party.experimentRunner.test_experimentRunner import rig, execute, executeSuite, suiteDefinition, stream, definition
from tests.first_party.experimentRunner.test_archive import archivePath, freshChild, readArchive

reporting = importlib.import_module('first-party.applications.experimentRunner.reporting')
exporting = importlib.import_module('first-party.applications.experimentRunner.exporting')
run = importlib.import_module('first-party.applications.experimentRunner.run')


def selection(value):
    """Use actual archive-issued unit/turn identities rather than guessed labels."""
    unit = readArchive(value)['units'][0]
    return archivePath(value), unit['unitId'], unit['turns'][0]['turnId']


def compareSame(value, mode):
    """Select one exact field from the same observation, explicitly labelled as such."""
    selected = selection(value)
    return reporting.compare(*selected, *selected, mode=mode)


@pytest.mark.parametrize('mode', reporting.MODES)
def test_exact_mode_binds_original_commit_and_labels_same_observation(rig, mode):
    """Equal evidence binds the real COMMIT bytes, not a reconstructed weaker journal."""
    value, _ = execute(rig)
    comparison = compareSame(value, mode)
    assert comparison['result'] == 'Equal'
    assert comparison['alignment']['sameObservation'] is True
    commit = json.loads((archivePath(value) / 'records.log').read_bytes().splitlines()[-1])
    assert comparison['sources'][0]['commitSha256'] == commit['commitSha256']
    assert comparison['difference']['firstDifference'] is None
    text = exporting.jsonBytes(comparison).decode()
    assert ' amber ' not in text and str(archivePath(value)) not in text


def test_missing_selection_or_source_is_not_equal(rig):
    """Missing identifiers or unavailable archives cannot equal each other."""
    value, _ = execute(rig)
    path, unit, turn = selection(value)
    result = reporting.compare(path, unit, 'missing', path, unit, 'missing', mode=reporting.MODES[0])
    assert result['result'] == 'NotComparable' and result['reasons'] == ['selection-missing']
    result = reporting.compare(path / 'missing', unit, turn, path / 'missing', unit, turn, mode=reporting.MODES[0])
    assert result['result'] == 'NotComparable'


def test_corrupt_source_not_comparable_but_prefix_report_qualified(rig):
    """An available completed answer in a corrupt prefix never permits equality."""
    value, _ = execute(rig)
    with (archivePath(value) / 'records.log').open('ab') as target:
        target.write(b'{bad}\n')
    assert compareSame(value, reporting.MODES[0])['reasons'] == ['source-invalid']
    result = reporting.report(archivePath(value))
    assert result['scopeCompleteness'] == 'invalid-prefix' and exporting.exitCode(result) == 2
    assert result['counts']['turnStatuses'] == {'succeeded': 2}


def test_torn_tail_and_selected_private_original_body_are_distinct(rig):
    """Valid incomplete scope retains evidence without changing its source outcome."""
    value, _ = execute(rig)
    path, unit, turn = selection(value)
    with (path / 'records.log').open('ab') as target:
        target.write(b'{')
    assert compareSame(value, reporting.MODES[2])['result'] == 'Equal'
    result = reporting.report(path, unitIds=[unit], turnIds=[turn], includeContent=True)
    assert result['scopeCompleteness'] == 'incomplete-prefix'
    assert result['content'][0]['answer'] == ' amber '
    import base64
    body = base64.b64decode(result['content'][0]['requestBody']['value'], validate=True)
    assert body == [b for url, b in rig[0]['requestBytes'] if url.endswith('/completions')][0]
    with pytest.raises(reporting.ReportError):
        exporting.csvBytes(result)


def test_summary_counts_metrics_and_exclusive_manifest_last_export(rig, tmp_path):
    """Aggregates retain definitions/denominators; collision cannot overwrite bytes."""
    value, _ = executeSuite(rig, suiteDefinition(rig))
    source = archivePath(value)
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    result = reporting.report(source)
    assert result['counts']['plannedTurns'] == result['counts']['transportAttempts'] == 8
    assert result['metrics'][0]['contributing'] == 8
    marker = exporting.publish(result, tmp_path, 'summary', kind='report', format='json', sourceDirectories=[source])
    assert marker['publicationState'] == 'complete'
    raw = (tmp_path / 'summary' / 'report.json').read_bytes()
    assert reporting.fmt.digest(raw) == marker['data']['sha256']
    assert b'Remember the word' not in raw and b' amber ' not in raw
    with pytest.raises(FileExistsError):
        exporting.publish(result, tmp_path, 'summary', kind='report', format='json', sourceDirectories=[source])
    assert before == {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    assert len(exporting.csvBytes(result).splitlines()) == 9


def test_failed_marker_publication_leaves_no_completed_export(rig, tmp_path, monkeypatch):
    """A fully written data file is not a publication when the final marker fails."""
    value, _ = execute(rig)
    original = exporting.stage
    def fail(directory, filename, raw):
        """Fail exactly the manifest stage after the actual data-file publication."""
        if filename == 'archive-manifest.json':
            raise OSError('private-error-sentinel')
        original(directory, filename, raw)
    monkeypatch.setattr(exporting, 'stage', fail)
    with pytest.raises(OSError):
        exporting.publish(reporting.report(archivePath(value)), tmp_path, 'failed', kind='report', format='json', sourceDirectories=[archivePath(value)])
    assert (tmp_path / 'failed' / 'report.json').is_file()
    assert not (tmp_path / 'failed' / 'archive-manifest.json').exists()


def test_csv_formula_and_terminal_controls_are_escaped():
    """Safe presentation preserves exact private JSON while containing terminal/spreadsheet controls."""
    assert exporting.safeCell('=1+1').startswith("'")
    assert exporting.safeCell(' \t=1').startswith("'")
    assert '\u202e' not in exporting.safeCell('\u202eunsafe\x1b[2J')
    assert '\\u202e' in exporting.safeCell('\u202eunsafe')
    with pytest.raises(reporting.ReportError):
        exporting.safeCell('x' * 4097)


def test_offline_commands_need_no_backend_assets_network_or_writes(rig, monkeypatch):
    """Fresh report/comparison audit denies runtime access and all active IO."""
    value, _ = execute(rig)
    path, unit, turn = selection(value)
    code = r'''
import sys, importlib, json
sys.dont_write_bytecode = True
r = importlib.import_module('first-party.applications.experimentRunner.reporting')
e = importlib.import_module('first-party.applications.experimentRunner.exporting')
importlib.import_module('first-party.applications.experimentRunner.definition')
def audit(event, args):
    """Reject active IO after the offline code bootstrap."""
    if event.startswith(('socket.', 'subprocess.')):
        raise AssertionError('active IO')
    if event == 'open':
        mode, flags = args[1:3]
        if isinstance(mode, str) and any(c in mode for c in 'wax+') or isinstance(flags, int) and flags & 3:
            raise AssertionError('write')
sys.addaudithook(audit)
selected = sys.argv[1:4]
assert r.compare(*selected, *selected, mode=r.MODES[2])['result'] == 'Equal'
print(e.jsonBytes(r.report(selected[0])).decode())
assert not any(n == 'backend' or n.startswith('backend.') for n in sys.modules)
'''
    child = freshChild(monkeypatch, code, path, unit, turn)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)['scopeCompleteness'] == 'sealed-source'


def test_cli_explicit_selection_and_fixed_error_does_not_echo_path(rig, capsys):
    """CLI errors use diagnostic exit2 and never print private input paths."""
    value, _ = execute(rig)
    path, unit, turn = selection(value)
    assert run.main(['compare', '--left', str(path), '--left-unit', unit, '--left-turn', turn,
                     '--right', str(path), '--right-unit', unit, '--right-turn', turn, '--mode', reporting.MODES[0]]) == 0
    assert 'Equal' in capsys.readouterr().out
    assert run.main(['report', '--archive', 'private-sentinel-not-present']) == 2
    captured = capsys.readouterr()
    assert 'private-sentinel' not in captured.err + captured.out


def test_unequal_answers_do_not_imply_input_or_body_equality(rig):
    """Different repetitions have exact unequal outputs while first-call inputs match."""
    value, _ = executeSuite(rig, suiteDefinition(rig))
    path = archivePath(value)
    units = readArchive(value)['units']
    result = reporting.compare(path, units[0]['unitId'], 't1', path, units[1]['unitId'], 't1', mode=reporting.MODES[0])
    assert result['result'] == 'NotEqual' and result['difference']['firstDifference'] == 7
    assert not result['alignment']['sameObservation']
    assert len(set(result['alignment']['inputSignatures'])) == 1
    assert len(set(result['alignment']['querySha256'])) == 1
    reporting.reportSchema.validate(result)


def test_empty_answer_and_unexposed_reasoning_are_not_conflated(rig):
    """An empty completed answer compares equal; missing reasoning is unavailable."""
    rig[0]['streams'][0] = stream('', reasoning=None)
    value, _ = execute(rig)
    answer = compareSame(value, reporting.MODES[0])
    assert answer['result'] == 'Equal' and answer['difference']['lengths'] == [0, 0]
    reasoning = compareSame(value, reporting.MODES[1])
    assert reasoning['result'] == 'NotComparable' and reasoning['reasons'] == ['reasoning-unavailable']
    reporting.reportSchema.validate(reasoning)


def test_input_signature_mismatch_requires_explicit_cross_input_flag(rig):
    """Different user prefixes cannot be silently compared, even for equal answers."""
    from tests.first_party.experimentRunner.test_experimentRunner import launcher
    first, _ = execute(rig)
    rig[0]['streams'].extend([stream(' amber '), stream('answer two')])
    different = definition()
    different['conversation']['turns'][0]['content'] = 'Different private input'
    second, _ = launcher.execute(launcher.prepare(different, rig[1]), rig[2], handleInterrupt=False)
    args = (*selection(first), *selection(second.value()))
    result = reporting.compare(*args, mode=reporting.MODES[0])
    assert result['result'] == 'NotComparable' and result['reasons'] == ['input-signature-mismatch']
    result = reporting.compare(*args, mode=reporting.MODES[0], allowInputDifferences=True)
    assert result['result'] == 'Equal' and len(set(result['alignment']['inputSignatures'])) == 2


def test_report_caps_reject_before_full_reader_and_export_cannot_modify_source(rig, monkeypatch):
    """Budget overflow is explicit before validation; publishing beneath source is refused."""
    value, _ = execute(rig)
    path = archivePath(value)
    result = reporting.report(path)
    with pytest.raises(reporting.ReportError):
        exporting.publish(result, path, 'export', kind='report', format='json', sourceDirectories=[path])
    assert not (path / 'export').exists()
    headerPath = path / 'archive-manifest.json'
    header = reporting.fmt.decode(headerPath.read_bytes())
    header['limits']['archiveBytes'] = 513 * reporting.MIB
    headerPath.write_bytes(reporting.fmt.canonical(header))
    def forbidden(*args, **kwargs):
        """Catch accidental full archive inspection after source budget rejection."""
        raise AssertionError('full read after cap')
    monkeypatch.setattr(reporting.reader, 'inspectArchive', forbidden)
    with pytest.raises(reporting.ReportError):
        reporting.report(path)


@pytest.mark.parametrize('fault', ['metric-unit', 'aggregate-sum', 'source-sequence', 'cleanup-extra'])
def test_typed_output_validation_rejects_nested_contradictions(rig, fault):
    """Closed top-level fields cannot hide false units/arithmetic/binding/private extras."""
    value, _ = execute(rig)
    result = reporting.report(archivePath(value))
    if fault == 'metric-unit':
        result['rows'][0]['metrics'][0]['unit'] = 'tokens'
    elif fault == 'aggregate-sum':
        result['metrics'][0]['sum'] = '0'
    elif fault == 'source-sequence':
        result['sources'][0]['committedSequence'] = 0
    else:
        result['rows'][0]['callCleanup']['private'] = 'secret'
    with pytest.raises(reporting.fmt.ArchiveError):
        exporting.publish(result, rig[2], 'invalid', kind='report', format='json', sourceDirectories=[archivePath(value)])
