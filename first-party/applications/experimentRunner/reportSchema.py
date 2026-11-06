# file: first-party/applications/experimentRunner/reportSchema.py ; version: 1
"""Closed typed offline report values; generated diagnostics carry no live authority."""
import importlib
import base64
import re

fmt = importlib.import_module('first-party.applications.experimentRunner.archiveFormat')


def enum(value, choices):
    """Require a builtin string from an exact closed value set."""
    fmt.require(type(value) is str and value in choices)


def decimal(value):
    """Require lossless nonnegative decimal integers, never floats or boolean values."""
    fmt.require(type(value) is str and len(value) <= 128 and re.fullmatch('0|[1-9][0-9]*', value) is not None)


def nullableHash(value):
    """Keep unavailable identity distinct from a manufactured digest."""
    if value is not None:
        fmt.hashValue(value)


def source(value):
    """Validate complete immutable source binding and status qualifiers."""
    fmt.fields(value, ('schemaId', 'archiveId', 'suiteId', 'manifestSha256', 'committedSequence', 'commitSha256',
                       'archiveStatus', 'suiteOutcome', 'validation'))
    fmt.require(value['schemaId'] == fmt.PREFIX + 'source@1')
    for key in ('archiveId', 'suiteId'):
        fmt.identifier(value[key], opaque=True)
    fmt.hashValue(value['manifestSha256'])
    fmt.integer(value['committedSequence'], 0, 65536)
    nullableHash(value['commitSha256'])
    fmt.require((value['committedSequence'] == 0) == (value['commitSha256'] is None))
    enum(value['archiveStatus'], ('sealed', 'incomplete', 'corrupt'))
    fmt.require(value['suiteOutcome'] is None or value['suiteOutcome'] in ('succeeded', 'failed', 'cancelled'))
    enum(value['validation'], ('validated-prefix', 'invalid-prefix'))
    fmt.require((value['archiveStatus'] == 'corrupt') == (value['validation'] == 'invalid-prefix'))


def availability(value):
    """Validate each evidence mode's independent availability flag."""
    fmt.fields(value, ('answer', 'reasoning', 'requestBody'))
    fmt.require(all(type(flag) is bool for flag in value.values()))


def metric(value):
    """Validate a closed measurement identifier/unit/definition with explicit absence."""
    fmt.fields(value, ('schemaId', 'metricId', 'unit', 'value', 'availability', 'reason', 'measurementProvenance'))
    fmt.require(value['schemaId'] == fmt.PREFIX + 'metric@1')
    definitions = {
        'application.turn-duration-ns@1': ('nanoseconds', 'app-monotonic-preparation-through-call-cleanup-before-turn-ack'),
        'native.completion-tokens@1': ('tokens', 'provider-reported-completion-tokens'),
        'pm.first-text-delivery-latency-ns@1': ('nanoseconds', None),
        'pm.delivered-unicode-characters@1': ('unicode-code-points', None),
    }
    fmt.require(value['metricId'] in definitions)
    unit, provenance = definitions[value['metricId']]
    fmt.require(value['unit'] == unit)
    if provenance is None:
        item = value['measurementProvenance']
        fmt.fields(item, ('definition', 'phase', 'sessionId', 'clockEpochId'))
        fmt.require(item['definition'] == 'pm-stream-delivery@1' and item['phase'] == 'call-final')
        for key in ('sessionId', 'clockEpochId'):
            if item[key] is not None:
                fmt.identifier(item[key], opaque=True)
        if value['value'] is not None:
            fmt.require(item['sessionId'] is not None and item['clockEpochId'] is not None)
    else:
        fmt.require(value['measurementProvenance'] == provenance)
    enum(value['availability'], ('available', 'unavailable'))
    enum(value['reason'], ('measured', 'not-completed', 'native-usage-unavailable', 'observation-unavailable'))
    fmt.require((value['availability'] == 'available') == (value['value'] is not None) == (value['reason'] == 'measured'))
    if value['value'] is not None:
        decimal(value['value'])


def aggregate(value, rows):
    """Validate exact rational summary arithmetic against its selected measurements."""
    fmt.fields(value, ('schemaId', 'metricId', 'unit', 'contributing', 'excluded', 'exclusionReasons', 'sum', 'min', 'max', 'mean'))
    fmt.require(value['schemaId'] == fmt.PREFIX + 'aggregate@1')
    fmt.integer(value['contributing'], 0, 256)
    fmt.integer(value['excluded'], 0, 256)
    fmt.require(value['contributing'] + value['excluded'] == len(rows))
    fmt.require(type(value['exclusionReasons']) is dict and set(value['exclusionReasons']) <= {'not-completed', 'native-usage-unavailable', 'observation-unavailable'})
    for count in value['exclusionReasons'].values():
        fmt.integer(count, 1, 256)
    fmt.require(sum(value['exclusionReasons'].values()) == value['excluded'])
    measured = [m for row in rows for m in row['metrics'] if m['metricId'] == value['metricId']]
    fmt.require(len(measured) == len(rows) and all(m['unit'] == value['unit'] for m in measured))
    numbers = [int(m['value']) for m in measured if m['value'] is not None]
    fmt.require(len(numbers) == value['contributing'])
    for key, expected in (('sum', sum(numbers)), ('min', min(numbers, default=0)), ('max', max(numbers, default=0))):
        fmt.require(value[key] == (str(expected) if numbers else None))
    if numbers:
        fmt.fields(value['mean'], ('numerator', 'denominator'))
        fmt.require(value['mean'] == dict(numerator=str(sum(numbers)), denominator=str(len(numbers))))
    else:
        fmt.require(value['mean'] is None)


def validate(value):
    """Reject malformed nested report values before presentation or export publication."""
    fmt.require(type(value) is dict)
    schema = value.get('schemaId')
    common = ('schemaId', 'generator', 'sources', 'selection', 'contentPolicy', 'content')
    if schema == fmt.PREFIX + 'report@1':
        fmt.fields(value, (*common, 'scopeCompleteness', 'counts', 'rows', 'metrics', 'diagnostics'))
    else:
        fmt.require(schema == fmt.PREFIX + 'comparison@1')
        fmt.fields(value, (*common, 'mode', 'result', 'reasons', 'alignment', 'fieldAvailability', 'difference', 'sourceOutcomes'))
    fmt.fields(value['generator'], ('implementationId', 'sourceVersion', 'applicationBaseRef'))
    fmt.require(value['generator']['implementationId'] == 'applications.experimentRunner.reporting')
    fmt.integer(value['generator']['sourceVersion'], 1, 1)
    fmt.require(type(value['generator']['applicationBaseRef']) is str and re.fullmatch('[0-9a-f]{40}', value['generator']['applicationBaseRef']))
    fmt.require(type(value['sources']) is list and len(value['sources']) <= 2)
    for item in value['sources']:
        source(item)
    enum(value['contentPolicy'], ('summary-only', 'selected-private'))
    fmt.require(type(value['content']) is list)
    if value['contentPolicy'] == 'summary-only':
        fmt.require(not value['content'])
    if schema == fmt.PREFIX + 'report@1':
        report(value)
    else:
        comparison(value)
    return value


def report(value):
    """Validate every planned row, selection, aggregate and optional private artifact."""
    fmt.require(len(value['sources']) == 1)
    fmt.fields(value['selection'], ('unitIds', 'turnIds'))
    for key in value['selection']:
        identifiers = value['selection'][key]
        fmt.require(type(identifiers) is list and len(identifiers) == len(set(identifiers)))
        for item in identifiers:
            fmt.identifier(item, opaque=key == 'unitIds')
    enum(value['scopeCompleteness'], ('sealed-source', 'incomplete-prefix', 'invalid-prefix'))
    fmt.require(value['scopeCompleteness'] == {'sealed': 'sealed-source', 'incomplete': 'incomplete-prefix', 'corrupt': 'invalid-prefix'}[value['sources'][0]['archiveStatus']])
    fmt.require(type(value['rows']) is list and len(value['rows']) <= 256)
    seen = set()
    for row in value['rows']:
        fmt.fields(row, ('schemaId', 'sourceIndex', 'unitId', 'configurationIndex', 'configurationName', 'profileAlias',
                         'repetition', 'turnId', 'turnOrdinal', 'unitStatus', 'jobOutcome', 'lifetimeBoundary', 'turnStatus',
                         'callCleanup', 'fieldAvailability', 'metrics', 'observationAvailability'))
        fmt.require(row['schemaId'] == fmt.PREFIX + 'row@1' and type(row['sourceIndex']) is int and row['sourceIndex'] == 0)
        fmt.identifier(row['unitId'], opaque=True)
        for key in ('configurationName', 'profileAlias', 'turnId'):
            fmt.identifier(row[key])
        for key, high in (('configurationIndex', 8), ('repetition', 10), ('turnOrdinal', 16)):
            fmt.integer(row[key], 1, high)
        identity = row['unitId'], row['turnId']
        fmt.require(identity not in seen)
        seen.add(identity)
        enum(row['unitStatus'], ('not-started', 'planned', 'succeeded', 'failed', 'cancelled'))
        enum(row['turnStatus'], ('not-started', 'planned', 'succeeded', 'failed', 'cancelled'))
        fmt.require(row['jobOutcome'] is None or row['jobOutcome'] in ('unavailable', 'Pending', 'Running', 'Succeeded', 'Failed', 'Cancelled'))
        enum(row['lifetimeBoundary'], ('not-started', 'unavailable', 'completed', 'failed-or-unknown'))
        fmt.fields(row['callCleanup'], ('status', 'quiescence'))
        enum(row['callCleanup']['status'], ('unavailable', 'not-required', 'confirmed', 'failed', 'unknown', 'pending', 'completed'))
        enum(row['callCleanup']['quiescence'], ('unknown', 'confirmed'))
        availability(row['fieldAvailability'])
        fmt.require(type(row['metrics']) is list and len(row['metrics']) == 4)
        for item in row['metrics']:
            metric(item)
        fmt.require(len({m['metricId'] for m in row['metrics']}) == 4)
        enum(row['observationAvailability'], ('available', 'unavailable'))
    fmt.fields(value['counts'], ('plannedTurns', 'turnStatuses', 'transportAttempts', 'finalMayHaveSent'))
    if value['counts']['plannedTurns'] is not None:
        fmt.integer(value['counts']['plannedTurns'], 0, 256)
        fmt.require(value['counts']['plannedTurns'] == len(value['rows']))
    expected = {}
    for row in value['rows']:
        expected[row['turnStatus']] = expected.get(row['turnStatus'], 0) + 1
    fmt.require(value['counts']['turnStatuses'] == expected)
    for key in ('transportAttempts', 'finalMayHaveSent'):
        fmt.integer(value['counts'][key], 0, len(value['rows']))
    fmt.require(type(value['metrics']) is list and len(value['metrics']) == 4)
    for item in value['metrics']:
        aggregate(item, value['rows'])
    fmt.fields(value['diagnostics'], ('ackObservation', 'writerCloseObservation', 'aggregateStatusStrata'))
    fmt.require(value['diagnostics'] == dict(ackObservation='unavailable', writerCloseObservation='unavailable', aggregateStatusStrata=expected))
    if value['contentPolicy'] == 'selected-private':
        fmt.require(len(value['content']) == len(value['rows']) == 1)
        item = value['content'][0]
        fmt.fields(item, ('unitId', 'turnId', 'answer', 'reasoning', 'requestBody', 'observations'))
        fmt.require((item['unitId'], item['turnId']) in seen)
        fmt.require(type(item['observations']) is list and len(item['observations']) <= 1)
        observationFormat = importlib.import_module('first-party.applications.experimentRunner.observationFormat')
        for observation in item['observations']:
            observationFormat.observation(observation)
            fmt.require(observation['unitId'] == item['unitId'] and observation['turnId'] == item['turnId'] and observation['phase'] == 'call-final')
            fmt.require(observation['suiteId'] == value['sources'][0]['suiteId'])
        for key in ('answer', 'reasoning'):
            fmt.require(item[key] is None or type(item[key]) is str)
        if item['requestBody'] is not None:
            body = item['requestBody']
            fmt.fields(body, ('encoding', 'bytes', 'sha256', 'value'))
            fmt.require(body['encoding'] == 'base64' and type(body['value']) is str)
            fmt.integer(body['bytes'], 0, 8 * 1024 * 1024)
            fmt.hashValue(body['sha256'])
            fmt.require(len(body['value']) == 4 * ((body['bytes'] + 2) // 3))
            raw = base64.b64decode(body['value'], validate=True)
            fmt.require(len(raw) == body['bytes'] and fmt.digest(raw) == body['sha256'])


def comparison(value):
    """Validate closed three-way comparisons and each typed evidence/alignment field."""
    fmt.require(value['contentPolicy'] == 'summary-only' and not value['content'])
    enum(value['mode'], tuple(fmt.PREFIX + item + '@1' for item in ('answer-text', 'reasoning-text', 'request-body')))
    enum(value['result'], ('Equal', 'NotEqual', 'NotComparable'))
    fmt.require(type(value['selection']) is list and len(value['selection']) == 2)
    for index, selection in enumerate(value['selection']):
        fmt.fields(selection, ('sourceIndex', 'unitId', 'turnId'))
        fmt.require(type(selection['sourceIndex']) is int and selection['sourceIndex'] == index)
        fmt.identifier(selection['unitId'], opaque=True)
        fmt.identifier(selection['turnId'])
    reasons = ('source-invalid', 'source-unavailable', 'unsupported-version', 'input-limit', 'selection-missing',
               'selection-ambiguous', 'input-signature-mismatch', 'turn-not-completed', 'answer-unavailable',
               'reasoning-unavailable', 'body-unavailable', 'field-available')
    fmt.require(type(value['reasons']) is list and 1 <= len(value['reasons']) <= 2)
    for item in value['reasons']:
        enum(item, reasons)
    fmt.require(type(value['fieldAvailability']) is list and len(value['fieldAvailability']) in (0, 2))
    for item in value['fieldAvailability']:
        availability(item)
    if value['alignment'] is not None:
        alignment = value['alignment']
        fmt.fields(alignment, ('inputSignatures', 'querySha256', 'requestedOptionsSha256', 'configurationSha256',
                               'provenanceSha256', 'allowInputDifferences', 'sameObservation'))
        for key in ('inputSignatures', 'querySha256', 'requestedOptionsSha256', 'configurationSha256', 'provenanceSha256'):
            fmt.require(type(alignment[key]) is list and len(alignment[key]) == 2)
            for item in alignment[key]:
                nullableHash(item)
        fmt.require(type(alignment['allowInputDifferences']) is bool and type(alignment['sameObservation']) is bool)
    fmt.require(type(value['sourceOutcomes']) is list and len(value['sourceOutcomes']) == len(value['sources']))
    for outcome, binding in zip(value['sourceOutcomes'], value['sources'], strict=True):
        fmt.require(outcome == dict(archiveStatus=binding['archiveStatus'], suiteOutcome=binding['suiteOutcome']))
    if value['result'] == 'NotComparable':
        fmt.require(value['difference'] is None and 'field-available' not in value['reasons'])
    else:
        fmt.require(len(value['sources']) == 2 and value['alignment'] is not None and value['reasons'] == ['field-available'])
        difference = value['difference']
        fmt.fields(difference, ('unit', 'lengths', 'sha256', 'firstDifference'))
        fmt.require(difference['unit'] == ('bytes' if value['mode'].endswith('request-body@1') else 'unicode-code-points'))
        fmt.require(type(difference['lengths']) is list and len(difference['lengths']) == 2)
        for item in difference['lengths']:
            fmt.integer(item, 0, 8 * 1024 * 1024)
        fmt.require(type(difference['sha256']) is list and len(difference['sha256']) == 2)
        for item in difference['sha256']:
            fmt.hashValue(item)
        if value['result'] == 'Equal':
            fmt.require(difference['firstDifference'] is None and difference['lengths'][0] == difference['lengths'][1]
                        and difference['sha256'][0] == difference['sha256'][1])
        else:
            fmt.integer(difference['firstDifference'], 0, min(difference['lengths']))
