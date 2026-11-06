# file: tests/first_party/experimentRunner/test_reviewEx05R2.py ; version: 1
"""Default terminal views must expose selected metrics and incomplete-source qualifiers."""
from tests.first_party.experimentRunner.test_reporting import (
    rig, execute, reporting, exporting, selection,
)
from tests.first_party.experimentRunner.test_archive import archivePath


def test_default_report_table_shows_measurements(rig):
    """The requested outcome/metric surface cannot discard every retained measurement."""
    value, _ = execute(rig)
    report = reporting.report(archivePath(value))
    rendered = exporting.table(report)
    for metric in report['metrics']:
        assert metric['metricId'] in rendered
    assert report['rows'][0]['metrics'][0]['value'] in rendered


def test_default_equal_comparison_shows_incomplete_source(rig):
    """Valid selected equality must keep the incomplete archive qualification visible."""
    value, _ = execute(rig)
    path, unit, turn = selection(value)
    with (path / 'records.log').open('ab') as target:
        target.write(b'{')
    comparison = reporting.compare(path, unit, turn, path, unit, turn, mode=reporting.MODES[2])
    assert comparison['result'] == 'Equal'
    assert all(source['archiveStatus'] == 'incomplete' for source in comparison['sources'])
    assert 'incomplete' in exporting.table(comparison)
