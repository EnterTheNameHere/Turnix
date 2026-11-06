# file: first-party/applications/experimentRunner/observationFormat.py ; version: 2
"""Stdlib typed App observation archive projection; no collectors or runtime imports."""
import importlib
import math
import re

fmt = importlib.import_module('first-party.applications.experimentRunner.archiveFormat')
REQUEST_KEYS = ('memory', 'cpu', 'gpu', 'stream', 'sampleIntervalSeconds', 'historySamples', 'windowSeconds', 'historyWindows')
STATES = ('completed', 'pending', 'failed', 'unavailable', 'not-requested')
REASONS = ('captured', 'not-selected', 'setup-unavailable', 'snapshot-unavailable', 'over-limit')
MEASUREMENT = 'pm-selected-stream-delivery-and-whole-host-collection@1'


def enum(value, choices):
    """Require a builtin member of one closed vocabulary."""
    fmt.require(type(value) is str and value in choices)


def decimal(value, *, nullable=False):
    """Accept bounded nonnegative decimal evidence without float coercion."""
    if nullable and value is None:
        return
    fmt.require(type(value) is str and len(value) <= 128 and re.fullmatch('0|[1-9][0-9]*', value) is not None)


def number(value, low, high):
    """Validate finite JSON numeric controls, excluding bool and overflow."""
    fmt.require(type(value) in (int, float) and low <= value <= high and math.isfinite(value))


def call(value):
    """Validate one explicit configuration/repetition/turn selection."""
    fmt.fields(value, ('configurationIndex', 'repetition', 'turnId'))
    fmt.integer(value['configurationIndex'], 1, 8)
    fmt.integer(value['repetition'], 1, 10)
    fmt.identifier(value['turnId'])
    return value['configurationIndex'], value['repetition'], value['turnId']


def request(value):
    """Keep needs, cadence and retention finite before any facility is requested."""
    fmt.fields(value, REQUEST_KEYS)
    fmt.require(all(type(value[key]) is bool for key in ('memory', 'cpu', 'gpu', 'stream')))
    for key in ('sampleIntervalSeconds', 'windowSeconds'):
        number(value[key], .25, 30)
    for key in ('historySamples', 'historyWindows'):
        fmt.integer(value[key], 1, 128)


def policy(value):
    """Validate complete policy without retaining live origins, endpoints or credentials."""
    fmt.fields(value, ('schemaId', 'enabled', 'calls', 'request', 'liveSelection', 'retention', 'effectiveLiveAvailability'))
    fmt.require(value['schemaId'] == fmt.PREFIX + 'observation-policy@1' and type(value['enabled']) is bool)
    fmt.require(type(value['calls']) is list and len(value['calls']) <= 8)
    selected = [call(item) for item in value['calls']]
    fmt.require(len(set(selected)) == len(selected) and (value['enabled'] or not selected))
    request(value['request'])
    live = value['liveSelection']
    fmt.fields(live, ('requested', 'call', 'endpointPolicy', 'originPolicy', 'maxLifetimeSeconds'))
    fmt.require(type(live['requested']) is bool)
    fmt.require(live['endpointPolicy'] == 'loopback-ephemeral' and live['originPolicy'] == 'exact-loopback')
    number(live['maxLifetimeSeconds'], .25, 30)
    if live['requested']:
        fmt.require(call(live['call']) in selected)
    else:
        fmt.require(live['call'] is None)
    fmt.require(value['retention'] == dict(maxSnapshots=9, maxBytes=32 * 1024 * 1024,
        deadlineOrigin='before-session-request', expiryPolicy='viewer-only-in-flight-then-source-tail'))
    enum(value['effectiveLiveAvailability'], ('not-requested', 'pending-activation', 'available', 'no-private-handoff', 'bridge-unavailable'))
    fmt.require(live['requested'] or value['effectiveLiveAvailability'] == 'not-requested')


def signal(value, unit, integral=False):
    """Check exact allowlisted numeric signals, units and unavailable values."""
    fmt.fields(value, ('availability', 'unit', 'value'))
    fmt.require(value['unit'] == unit)
    enum(value['availability'], ('available', 'unavailable'))
    fmt.require((value['availability'] == 'unavailable') == (value['value'] is None))
    if value['value'] is not None:
        if integral:
            decimal(value['value'])
        else:
            number(value['value'], -(2 ** 53 - 1), 2 ** 53 - 1)


def window(value):
    """Validate stream half-open intervals, delivered-character counts and exact gaps."""
    keys = ('startNs', 'endNs', 'characterCount', 'deliveryCount', 'firstTextNs', 'lastTextNs',
            'gapCount', 'gapSumNs', 'minGapNs', 'maxGapNs')
    fmt.fields(value, (*keys, 'partial', 'charactersPerSecond'))
    for key in keys:
        decimal(value[key], nullable=key in ('firstTextNs', 'lastTextNs', 'minGapNs', 'maxGapNs'))
    fmt.require(int(value['endNs']) >= int(value['startNs']) and type(value['partial']) is bool)
    signal(value['charactersPerSecond'], 'unicode-code-points/second')
    deliveryFacts(value)
    start, end = int(value['startNs']), int(value['endNs'])
    if value['firstTextNs'] is not None:
        fmt.require(start <= int(value['firstTextNs']) <= int(value['lastTextNs']) <= end)
        fmt.require(value['partial'] or int(value['lastTextNs']) < end)
    count, gaps = int(value['deliveryCount']), int(value['gapCount'])
    fmt.require(max(0, count - 1) <= gaps <= count)
    if gaps == 0:
        fmt.require(value['gapSumNs'] == '0' and value['minGapNs'] is None and value['maxGapNs'] is None)
    else:
        fmt.require(value['minGapNs'] is not None and value['maxGapNs'] is not None)
        low, high, total = int(value['minGapNs']), int(value['maxGapNs']), int(value['gapSumNs'])
        fmt.require(low <= high and low * gaps <= total <= high * gaps)
    # Gaps include the previous arrival even outside this window: no span cap.
    rate = int(value['characterCount']) * 1e9 / (end - start) if end > start else None
    encodedRate = rate if rate is not None and math.isfinite(rate) and abs(rate) <= 2 ** 53 - 1 else None
    fmt.require(value['charactersPerSecond']['value'] == encodedRate)


def deliveryFacts(value):
    """Keep empty delivery evidence distinct from missing first text or positive counts."""
    characters, deliveries = int(value['characterCount']), int(value['deliveryCount'])
    fmt.require(characters >= deliveries and (characters == 0) == (deliveries == 0))
    first, last = value['firstTextNs'], value['lastTextNs']
    if deliveries == 0:
        fmt.require(first is None and last is None)
    else:
        fmt.require(first is not None and last is not None and int(first) <= int(last))
        fmt.require(deliveries != 1 or first == last)


def streamFacts(value, windowSeconds, windowLimit):
    """Check same-epoch stream bounds and derived values without reconstructing lost history."""
    deliveryFacts(value)
    origin, through, end = (None if value[key] is None else int(value[key])
                            for key in ('originNs', 'observedThroughNs', 'endNs'))
    first, last = (None if value[key] is None else int(value[key]) for key in ('firstTextNs', 'lastTextNs'))
    active, released = value['active'], value['released']
    fmt.require(not released or not active)
    fmt.require(not active or value['outcome'] is None)
    if value['availability'] == 'unavailable':
        fmt.require(value['state'] == 'unavailable')
    else:
        fmt.require(value['state'] == ('not-started' if origin is None else 'active' if active else 'terminal'))
        if origin is None:
            fmt.require(not active and (value['outcome'] is None or released))
        elif not active:
            fmt.require(value['outcome'] is not None and (released or end is not None))
    if origin is None:
        fmt.require(through is None and end is None and first is None and last is None)
        fmt.require(value['firstTextLatencyNs'] is None and value['silenceNs'] is None and not value['windows'])
    else:
        fmt.require(through is not None and through >= origin)
        if end is not None:
            fmt.require(not active and origin <= end == through)
        if first is not None:
            fmt.require(origin <= first <= last <= through)
        fmt.require(value['firstTextLatencyNs'] == (None if first is None else str(first - origin)))
        fmt.require(value['silenceNs'] == str(through - (origin if last is None else last)))
    if released:
        fmt.require(not value['windows'] and value['characterCount'] == value['deliveryCount'] == '0')
    windows = value['windows']
    fmt.require(len(windows) <= windowLimit)
    period = int(windowSeconds * 1e9)
    chars = deliveries = 0
    previousEnd = None
    for index, item in enumerate(windows):
        window(item)
        start, stop = int(item['startNs']), int(item['endNs'])
        fmt.require(origin is not None and origin <= start <= stop <= through)
        fmt.require(previousEnd is None or previousEnd <= start)
        fmt.require(stop - start <= period and (item['partial'] or stop - start == period))
        fmt.require(not item['partial'] or index == len(windows) - 1)
        if item['firstTextNs'] is not None:
            fmt.require(first is not None and first <= int(item['firstTextNs']) <= int(item['lastTextNs']) <= last)
            if int(item['firstTextNs']) == first:
                fmt.require(int(item['gapCount']) == int(item['deliveryCount']) - 1 and
                            int(item['gapSumNs']) == int(item['lastTextNs']) - first)
            else:
                fmt.require(item['gapCount'] == item['deliveryCount'])
                fmt.require(int(item['lastTextNs']) - int(item['firstTextNs']) <=
                            int(item['gapSumNs']) <= int(item['lastTextNs']) - first)
        chars += int(item['characterCount'])
        deliveries += int(item['deliveryCount'])
        previousEnd = stop
    fmt.require(chars <= int(value['characterCount']) and deliveries <= int(value['deliveryCount']))
    if value['droppedWindowCount'] == '0':
        fmt.require(chars == int(value['characterCount']) and deliveries == int(value['deliveryCount']))
        if windows:
            fmt.require(int(windows[0]['startNs']) == origin)
            fmt.require(all(a['endNs'] == b['startNs'] for a, b in zip(windows, windows[1:])))
    if origin is not None and not released:
        fmt.require((bool(windows) and int(windows[-1]['endNs']) == through) or
                    (not windows and origin == through and value['deliveryCount'] == '0'))


def hardware(value):
    """Validate the complete G-01 numeric whole-host sample allowlist offline."""
    fmt.fields(value, ('collectionStartNs', 'collectionEndNs', 'displayWallTimeNs', 'targetIntervalSeconds',
                       'actualIntervalSeconds', 'memory', 'swap', 'cpu', 'gpu'))
    for key in ('collectionStartNs', 'collectionEndNs', 'displayWallTimeNs'):
        decimal(value[key], nullable=True)
    if value['collectionStartNs'] is not None and value['collectionEndNs'] is not None:
        fmt.require(int(value['collectionStartNs']) <= int(value['collectionEndNs']))
    for key in ('targetIntervalSeconds', 'actualIntervalSeconds'):
        if value[key] is not None:
            number(value[key], 0, 2 ** 53 - 1)
    for group, keys in (('memory', ('totalBytes', 'availableBytes', 'usedBytes', 'freeBytes', 'usagePercent')),
                        ('swap', ('totalBytes', 'usedBytes', 'freeBytes', 'usagePercent'))):
        fmt.fields(value[group], keys)
        for key in keys:
            signal(value[group][key], 'percent' if key == 'usagePercent' else 'bytes', key != 'usagePercent')
    cpu = value['cpu']
    fmt.fields(cpu, ('aggregatePercent', 'perLogicalCorePercent', 'perCoreUnavailable', 'droppedCoreCount', 'physicalCores', 'logicalCores'))
    signal(cpu['aggregatePercent'], 'percent')
    fmt.require(type(cpu['perLogicalCorePercent']) is list and len(cpu['perLogicalCorePercent']) <= 256)
    for item in cpu['perLogicalCorePercent']:
        signal(item, 'percent')
    fmt.require(type(cpu['perCoreUnavailable']) is bool)
    decimal(cpu['droppedCoreCount'])
    for key in ('physicalCores', 'logicalCores'):
        signal(cpu[key], 'cores', True)
    units = dict(deviceIndex=('index', True), totalVramBytes=('bytes', True), usedVramBytes=('bytes', True),
        freeVramBytes=('bytes', True), gpuUtilizationPercent=('percent', False), memoryControllerUtilizationPercent=('percent', False),
        temperatureCelsius=('celsius', False), powerDrawWatts=('watts', False), powerLimitWatts=('watts', False),
        graphicsClockMhz=('megahertz', True), memoryClockMhz=('megahertz', True), fanSpeedPercent=('percent', False))
    fmt.fields(value['gpu'], units)
    for key, unit in units.items():
        signal(value['gpu'][key], *unit)


def publicData(value):
    """Validate a versioned App copy of G-01's safe data, excluding publication authority."""
    fmt.fields(value, ('schemaId', 'sessionId', 'encodings', 'units', 'clock', 'scope', 'sourceLifetime', 'needs',
        'retention', 'sampleIntervalSeconds', 'windowSeconds', 'hardwareAvailability', 'streamAvailability',
        'collectionStopped', 'hardware', 'stream', 'correlation', 'correlationMeaning'))
    fmt.require(value['schemaId'] == 'actant.observation-publication@1')
    fmt.identifier(value['sessionId'], opaque=True)
    fmt.require(value['encodings'] == dict(integerEvidence='decimal-string', ratesAndPercent='finite-json-number'))
    fmt.require(value['units'] == dict(timestampsAndGaps='nanoseconds', streamCharacters='unicode-code-points',
                                      deliveries='events', displayWallTimeNs='unix-epoch-nanoseconds'))
    fmt.fields(value['clock'], ('kind', 'epochId', 'nanosecondsEncoding'))
    fmt.identifier(value['clock']['epochId'], opaque=True)
    fmt.require(value['clock']['kind'] == 'host-process-monotonic' and value['clock']['nanosecondsEncoding'] == 'decimal-string')
    fmt.require(value['scope'] == 'whole-host-hardware/application-selected-stream')
    enum(value['sourceLifetime'], ('run', 'application'))
    fmt.fields(value['needs'], ('memory', 'cpu', 'gpu', 'stream'))
    fmt.require(all(type(flag) is bool for flag in value['needs'].values()))
    retention = value['retention']
    fmt.fields(retention, ('hardwareSamples', 'streamWindows', 'hardwareMayBeTruncated', 'hardwareDroppedCount'))
    for key in ('hardwareSamples', 'streamWindows'):
        decimal(retention[key])
        fmt.require(1 <= int(retention[key]) <= 128)
    fmt.require(type(retention['hardwareMayBeTruncated']) is bool and retention['hardwareDroppedCount'] is None)
    for key in ('sampleIntervalSeconds', 'windowSeconds'):
        number(value[key], .25, 30)
    enum(value['hardwareAvailability'], ('unavailable', 'as-reported-per-signal'))
    enum(value['streamAvailability'], ('unavailable', 'disabled', 'as-reported'))
    fmt.require(type(value['collectionStopped']) is bool and type(value['hardware']) is list and len(value['hardware']) <= 128)
    for item in value['hardware']:
        hardware(item)
    if value['stream'] is not None:
        stream = value['stream']
        numeric = ('originNs', 'observedThroughNs', 'endNs', 'firstTextNs', 'lastTextNs', 'characterCount', 'deliveryCount',
                   'droppedWindowCount', 'firstTextLatencyNs', 'silenceNs')
        fmt.fields(stream, (*numeric, 'availability', 'state', 'outcome', 'active', 'released', 'windows'))
        for key in numeric:
            decimal(stream[key], nullable=key not in ('characterCount', 'deliveryCount', 'droppedWindowCount'))
        enum(stream['availability'], ('available', 'unavailable'))
        enum(stream['state'], ('unavailable', 'not-started', 'active', 'terminal'))
        fmt.require(stream['outcome'] is None or stream['outcome'] in ('completed', 'failed', 'cancelled', 'closed'))
        fmt.require(type(stream['active']) is bool and type(stream['released']) is bool)
        fmt.require(type(stream['windows']) is list and len(stream['windows']) <= 128)
        streamFacts(stream, value['windowSeconds'], int(retention['streamWindows']))
        fmt.require(value['needs']['stream'] and value['streamAvailability'] != 'disabled')
    elif not value['needs']['stream']:
        fmt.require(value['streamAvailability'] == 'disabled')
    else:
        fmt.require(value['streamAvailability'] == 'unavailable')
    fmt.require(type(value['correlation']) is list and len(value['correlation']) <= 128)
    windows = [] if value['stream'] is None else value['stream']['windows']
    fmt.require(len(value['correlation']) == len(windows))
    for item, streamWindow in zip(value['correlation'], windows, strict=True):
        fmt.fields(item, ('startNs', 'endNs', 'hardwareIndices', 'uncoveredIntervalsNs'))
        decimal(item['startNs'])
        decimal(item['endNs'])
        fmt.require(int(item['endNs']) >= int(item['startNs']))
        fmt.require(all(item[key] == streamWindow[key] for key in ('startNs', 'endNs')))
        fmt.require(type(item['hardwareIndices']) is list and len(item['hardwareIndices']) <= 128)
        fmt.require(len(set(item['hardwareIndices'])) == len(item['hardwareIndices']))
        for index in item['hardwareIndices']:
            fmt.integer(index, 0, len(value['hardware']) - 1)
        fmt.require(type(item['uncoveredIntervalsNs']) is list and len(item['uncoveredIntervalsNs']) <= 129)
        previousEnd = int(item['startNs'])
        for pair in item['uncoveredIntervalsNs']:
            fmt.require(type(pair) is list and len(pair) == 2)
            decimal(pair[0])
            decimal(pair[1])
            fmt.require(previousEnd <= int(pair[0]) < int(pair[1]) <= int(item['endNs']))
            previousEnd = int(pair[1])
    fmt.require(value['correlationMeaning'] == 'collection-interval-overlap; sequential-reads; no-interpolation')


def observation(value):
    """Validate immutable per-attempt observations and consistent actual clock/session identity."""
    fmt.fields(value, ('schemaId', 'observationId', 'suiteId', 'unitId', 'turnId', 'attemptId', 'providerCallId', 'phase',
                       'availability', 'reason', 'measurementDefinition', 'clockEpochId', 'sessionId', 'sourceLifetime', 'publicData'))
    fmt.require(value['schemaId'] == fmt.PREFIX + 'observation@1')
    for key in ('observationId', 'suiteId', 'unitId', 'attemptId'):
        fmt.identifier(value[key], opaque=True)
    fmt.identifier(value['turnId'])
    if value['providerCallId'] is not None:
        fmt.identifier(value['providerCallId'], opaque=True)
    enum(value['phase'], ('call-final', 'lifetime-final'))
    enum(value['availability'], ('available', 'unavailable'))
    enum(value['reason'], REASONS)
    fmt.require(value['measurementDefinition'] == MEASUREMENT)
    enum(value['sourceLifetime'], ('run', 'application'))
    if value['availability'] == 'available':
        fmt.require(value['reason'] == 'captured')
        publicData(value['publicData'])
        fmt.require(value['sessionId'] == value['publicData']['sessionId'] and
                    value['clockEpochId'] == value['publicData']['clock']['epochId'] and
                    value['sourceLifetime'] == value['publicData']['sourceLifetime'])
    else:
        fmt.require(value['reason'] != 'captured' and value['publicData'] is None and value['sessionId'] is None and value['clockEpochId'] is None)


def cleanup(value):
    """Validate separate source/read/publication/handoff cleanup outcomes."""
    fmt.fields(value, ('schemaId', 'observationId', 'sessionRelease', 'parentRevoke', 'publicationWithdraw', 'handoffWorkerClose', 'errors'))
    fmt.require(value['schemaId'] == fmt.PREFIX + 'observation-cleanup@1')
    fmt.identifier(value['observationId'], opaque=True)
    for key in ('sessionRelease', 'parentRevoke', 'publicationWithdraw', 'handoffWorkerClose'):
        enum(value[key], STATES)
    fmt.require(type(value['errors']) is list and len(value['errors']) <= 8)
    for error in value['errors']:
        fmt.fields(error, ('stage', 'category'))
        enum(error['stage'], ('setup', 'handoff', 'viewer-revoke', 'snapshot', 'source-release', 'withdraw', 'tail'))
        enum(error['category'], ('unavailable', 'failed', 'interrupted'))
