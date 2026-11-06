# file: tests/first_party/experimentRunner/test_ex05R2.py ; version: 1
"""Default CLI projections retain diagnostic context, privacy and a whole-output bound."""
import copy
import importlib
import json

import pytest

from tests.first_party.experimentRunner.test_reporting import (
    rig, execute, executeSuite, suiteDefinition, stream, definition, selection, reporting, exporting,
)
from tests.first_party.experimentRunner.test_observations import host4
from tests.first_party.experimentRunner.test_archive import archivePath
from tests.first_party.experimentRunner.test_experimentRunner import launcher

run = importlib.import_module('first-party.applications.experimentRunner.run')


def compareArgs(left, right, mode):
    """Build exact explicit CLI selection without enabling JSON or altering default alignment."""
    result = ['compare', '--mode', mode]
    for side, (path, unit, turn) in (('left', left), ('right', right)):
        result.extend(['--' + side, str(path), '--' + side + '-unit', unit, '--' + side + '-turn', turn])
    return result


def assertPrivateAbsent(text, source):
    """Reject retained private content or directory paths while allowing intentional hashes/counts."""
    for private in ('Remember the word amber', ' amber ', 'private reasoning', str(source)):
        assert private not in text


def test_default_report_cli_retains_each_metric_and_aggregate_definition(rig, capsys):
    """Available PM/native/App evidence and unavailable rows remain visible with exact denominators."""
    host4(rig)
    value, _ = execute(rig)
    source = archivePath(value)
    expected = reporting.report(source)
    assert run.main(['report', '--archive', str(source)]) == 0
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]
    assert not captured.err
    header, rows, aggregates = lines[0], lines[1:-1], lines[-1]
    for key in ('sources', 'selection', 'scopeCompleteness', 'counts', 'diagnostics'):
        assert header[key] == expected[key]
    assert header['contentPolicy'] == 'summary-only'
    assert rows == expected['rows']
    assert aggregates['aggregates'] == expected['metrics']
    assert rows[0]['metrics'][2]['availability'] == 'available'
    assert rows[1]['metrics'][2]['availability'] == 'unavailable'
    assert aggregates['aggregates'][2]['contributing'] == aggregates['aggregates'][2]['excluded'] == 1
    assert rows[0]['callCleanup'] == dict(status='acknowledged', quiescence='confirmed')
    assert len(captured.out.encode('ascii')) <= exporting.MAX_SUMMARY
    assertPrivateAbsent(captured.out, source)


def test_default_suite_projection_and_explicit_selection_keep_configuration_context(rig, capsys):
    """Configuration, repetition, turn ordinal, Job and lifetime context survive the projection."""
    value, _ = executeSuite(rig, suiteDefinition(rig))
    source = archivePath(value)
    report = reporting.report(source)
    rows = [json.loads(line) for line in exporting.table(report).splitlines()][1:-1]
    assert rows == report['rows'] and len(rows) == 8
    assert {(row['configurationIndex'], row['repetition']) for row in rows} == {(1, 1), (1, 2), (2, 1), (2, 2)}
    assert {row['turnOrdinal'] for row in rows} == {1, 2}
    selected = rows[-1]
    assert run.main(['report', '--archive', str(source), '--unit', selected['unitId'], '--turn', selected['turnId']]) == 0
    header, row, aggregates = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert header['selection'] == dict(unitIds=[selected['unitId']], turnIds=[selected['turnId']])
    assert row == selected
    assert all(metric['contributing'] + metric['excluded'] == 1 for metric in aggregates['aggregates'])


def test_default_equal_cli_preserves_incomplete_source_outcomes_and_same_observation(rig, capsys):
    """Narrow valid equality remains Equal while both incomplete source qualifiers stay visible."""
    value, _ = execute(rig)
    selected = selection(value)
    with (selected[0] / 'records.log').open('ab') as target:
        target.write(b'{')
    expected = reporting.compare(*selected, *selected, mode=reporting.MODES[2])
    assert run.main(compareArgs(selected, selected, reporting.MODES[2])) == 0
    captured = capsys.readouterr()
    actual = json.loads(captured.out)
    assert actual == expected and actual['result'] == 'Equal'
    assert actual['alignment']['sameObservation'] is True
    assert all(source['archiveStatus'] == 'incomplete' and source['suiteOutcome'] == 'succeeded' for source in actual['sources'])
    assert actual['fieldAvailability'][0]['requestBody'] is True
    assert actual['selection'][0]['unitId'] == selected[1]
    assert actual['mode'] == reporting.MODES[2] and not captured.err
    assertPrivateAbsent(captured.out, selected[0])


def test_default_cli_discloses_cross_input_opt_in_without_changing_comparison_policy(rig, capsys):
    """Explicitly different inputs keep their signatures and opt-in flag in the terminal result."""
    first, _ = execute(rig)
    rig[0]['streams'].extend([stream(' amber '), stream('answer two')])
    changed = definition()
    changed['conversation']['turns'][0]['content'] = 'SECOND_PRIVATE_INPUT_SENTINEL'
    second, _ = launcher.execute(launcher.prepare(changed, rig[1]), rig[2], handleInterrupt=False)
    left, right = selection(first), selection(second.value())
    args = compareArgs(left, right, reporting.MODES[0])
    assert run.main(args) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused['result'] == 'NotComparable' and refused['reasons'] == ['input-signature-mismatch']
    assert refused['alignment']['allowInputDifferences'] is False
    assert run.main(args + ['--allow-input-differences']) == 0
    text = capsys.readouterr().out
    allowed = json.loads(text)
    assert allowed['result'] == 'Equal' and allowed['alignment']['allowInputDifferences'] is True
    assert allowed['alignment']['sameObservation'] is False
    assert len(set(allowed['alignment']['inputSignatures'])) == 2
    assert len(set(allowed['alignment']['querySha256'])) == 2
    assert 'SECOND_PRIVATE_INPUT_SENTINEL' not in text
    assertPrivateAbsent(text, left[0])
    assertPrivateAbsent(text, right[0])


def test_default_not_equal_cli_exposes_field_scope_and_availability(rig, capsys):
    """Different measured answers retain NotEqual rather than disappearing behind summary labels."""
    value, _ = executeSuite(rig, suiteDefinition(rig))
    source = archivePath(value)
    rows = reporting.report(source)['rows']
    left, right = (source, rows[0]['unitId'], 't1'), (source, rows[2]['unitId'], 't1')
    assert run.main(compareArgs(left, right, reporting.MODES[0])) == 1
    text = capsys.readouterr().out
    actual = json.loads(text)
    assert actual['result'] == 'NotEqual' and actual['difference']['firstDifference'] is not None
    assert all(field['answer'] for field in actual['fieldAvailability'])
    assert all(source['archiveStatus'] == 'sealed' for source in actual['sources'])
    assert 'answer-0' not in text and 'answer-2' not in text


def test_default_unavailable_reasoning_cli_does_not_render_empty_equality(rig, capsys):
    """Unavailable reasoning and its reason/source context stay explicit in default output."""
    rig[0]['streams'][0] = stream('', reasoning=None)
    value, _ = execute(rig)
    selected = selection(value)
    assert run.main(compareArgs(selected, selected, reporting.MODES[1])) == 2
    actual = json.loads(capsys.readouterr().out)
    assert actual['result'] == 'NotComparable' and actual['reasons'] == ['reasoning-unavailable']
    assert all(field['reasoning'] is False for field in actual['fieldAvailability'])
    assert actual['sourceOutcomes'] == [dict(archiveStatus='sealed', suiteOutcome='succeeded')] * 2


def test_private_json_input_is_still_summary_only_and_terminal_strings_are_escaped(rig):
    """Rendering drops private content even for an explicit private report and escapes control/bidi labels."""
    changed = definition()
    name = 'config\x1b[2J\n\u202e'
    changed['configurations'][0]['name'] = name
    value, _ = launcher.execute(launcher.prepare(changed, rig[1]), rig[2], handleInterrupt=False)
    source, unit, turn = selection(value.value())
    private = reporting.report(source, unitIds=[unit], turnIds=[turn], includeContent=True)
    assert private['content'][0]['answer'] == ' amber '
    before = copy.deepcopy(private)
    rendered = exporting.table(private)
    assert private == before
    assert rendered.isascii() and '\x1b' not in rendered and '\u202e' not in rendered
    assert '\\u001b' in rendered and '\\u202e' in rendered
    header, row, _ = [json.loads(line) for line in rendered.splitlines()]
    assert row['configurationName'] == name and header['contentPolicy'] == 'summary-only'
    assert 'requestBody' not in header and 'content' not in header
    assertPrivateAbsent(rendered, source)
    assert private['content'][0]['requestBody']['value'] not in rendered


def test_table_whole_output_bound_counts_delimiters_before_cli_writes(rig, capsys, monkeypatch):
    """Individually bounded lines cannot exceed the whole-output cap or leak partial stdout."""
    value, _ = execute(rig)
    source = archivePath(value)
    report = reporting.report(source)
    rendered = exporting.table(report)
    size = len(rendered.encode('ascii'))
    monkeypatch.setattr(exporting, 'MAX_SUMMARY', size)
    assert exporting.table(report) == rendered
    monkeypatch.setattr(exporting, 'MAX_SUMMARY', size - 1)
    with pytest.raises(reporting.ReportError):
        exporting.table(report)
    # Every individual line still fits: only the cumulative projection must reject.
    assert max(len(line) for line in rendered.splitlines()) < size - 1
    assert run.main(['report', '--archive', str(source)]) == 2
    captured = capsys.readouterr()
    assert not captured.out and str(source) not in captured.err and 'failed' in captured.err


def test_comparison_table_uses_summary_bound_and_invalid_source_stays_qualified(rig, monkeypatch):
    """Comparison size failures are explicit; corruption never becomes a successful field summary."""
    value, _ = execute(rig)
    selected = selection(value)
    comparison = reporting.compare(*selected, *selected, mode=reporting.MODES[2])
    size = len(exporting.table(comparison).encode('ascii'))
    with monkeypatch.context() as patch:
        patch.setattr(exporting, 'MAX_SUMMARY', size - 1)
        with pytest.raises(reporting.ReportError):
            exporting.table(comparison)
    with (selected[0] / 'records.log').open('ab') as target:
        target.write(b'{bad}\n')
    invalid = reporting.compare(*selected, *selected, mode=reporting.MODES[2])
    actual = json.loads(exporting.table(invalid))
    assert actual['result'] == 'NotComparable' and actual['reasons'] == ['source-invalid']
    assert all(source['validation'] == 'invalid-prefix' for source in actual['sources'])
    assert actual['difference'] is None
