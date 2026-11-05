# file: first-party/applications/experimentRunner/archiveFormat.py ; version: 1
"""Strict, standard-library-only archive grammar; no execution or runtime imports."""
from __future__ import annotations

import hashlib
import json
import math
import re
import importlib
import copy

PREFIX = 'actant.experiment.'
DEFAULTS = dict(archiveBytes=256 * 1024 * 1024, logicalRecords=8192,
    frameBytes=65536, artifactBytes=8 * 1024 * 1024, requestBytes=2 * 1024 * 1024,
    pendingBytes=65536, inspectBytes=16 * 1024 * 1024, turnEvents=65536,
    partialEvents=64, partialCharacters=4096, partialSeconds=1.0,
    partialRecords=128, ackSeconds=5.0)
MAXIMA = dict(archiveBytes=1024 ** 3, logicalRecords=65536, frameBytes=65536,
    artifactBytes=8 * 1024 * 1024, requestBytes=8 * 1024 * 1024,
    pendingBytes=262144, inspectBytes=64 * 1024 * 1024, turnEvents=262144,
    partialEvents=65536, partialCharacters=65536, partialSeconds=30.0,
    partialRecords=1024, ackSeconds=30.0)
REF_KEYS = ('schemaId', 'sha256', 'bytes', 'mediaType', 'encoding')
DATA_KEYS = ('schemaId', 'sequence', 'previousCommitHash', 'kind', 'archiveId', 'suiteId',
    'unitId', 'turnId', 'observedTimeNs', 'payload', 'artifacts', 'dataSha256')
COMMIT_KEYS = ('schemaId', 'sequence', 'dataSha256', 'previousCommitHash', 'commitSha256')
PAYLOADS = {
    'suite-planned': ('definitionRef', 'planRef', 'initialProvenanceRef'),
    'unit-planned': ('jobId', 'configurationIndex', 'repetition', 'profileAlias', 'requestedOptionsRef', 'provenanceRef'),
    'turn-planned': ('queryRef', 'requestedOptionsRef', 'attemptId'),
    'request-evidence': ('attemptId', 'providerCallId', 'phase', 'bodyRef', 'length', 'sha256',
        'mayHaveSent', 'responseHeadersObserved', 'responseBodyObserved', 'serverConsumption'),
    'partial': ('attemptId', 'deltaRef', 'eventFrom', 'eventTo', 'answerOffset', 'reasoningOffset', 'partial'),
    'turn-completed': ('resultRef',), 'unit-outcome': ('resultRef',),
    'suite-outcome': ('summaryRef',),
    'cleanup-observed': ('component', 'observedOutcome', 'evidenceRef'),
    'archive-seal': ('lastObservedSuiteStatus', 'terminalSequence', 'finalizationObserved')}
TURN_KEYS = ('turnId', 'input', 'status', 'query', 'requestedOptions', 'answer', 'reasoning',
    'native', 'executionProfile', 'executionEvidence', 'cleanup', 'startedTimeNs',
    'endedTimeNs', 'durationNs', 'error', 'partial')


class ArchiveError(RuntimeError):
    """Content-free archive failure; no filesystem error/path/payload chain."""


def require(condition):
    """Reject unsupported shapes with one private-content-free error."""
    if not condition:
        raise ArchiveError('Archive validation failed.')


def fields(value, keys):
    """Require exact builtin object fields, rejecting extensions and duplicates."""
    require(type(value) is dict and set(value) == set(keys))


def canonical(value):
    """Produce the sole compact, sorted-key UTF-8 hash representation."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(data):
    """Hash original bytes rather than convenience parsed representations."""
    return hashlib.sha256(data).hexdigest()


def hashWithout(value, key):
    """Hash the canonical object excluding only its declared digest field."""
    return digest(canonical({k: v for k, v in value.items() if k != key}))


def decode(data):
    """Reject duplicate keys, nonfinite values, non-UTF8 and malformed JSON."""
    def pairs(items):
        """Check uniqueness at each depth before materializing a mapping."""
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def constant(value):
        """Reject NaN and infinity without echoing the input."""
        raise ArchiveError('Archive validation failed.')
    def floating(value):
        """Reject numeric exponent overflow as well as literal infinity."""
        number = float(value)
        require(math.isfinite(number))
        return number
    error = False
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs,
            parse_constant=constant, parse_float=floating)
    except Exception:
        error = True
    if error:
        raise ArchiveError('Archive JSON is invalid.')


def integer(value, low=0, high=2 ** 63 - 1):
    """Check bounded integer values without accepting bool."""
    require(type(value) is int and low <= value <= high)


def identifier(value, *, opaque=False):
    """Validate IDs without filesystem or execution interpretation."""
    require(type(value) is str and 0 < len(value) <= 128)
    if opaque:
        require(re.fullmatch('[a-f0-9]{32}', value) is not None)


def hashValue(value):
    """Require a canonical digest usable only as a fixed blob filename."""
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None)


def limits(value):
    """Validate all trusted bounds; archived limits may never exceed hard ceilings."""
    fields(value, DEFAULTS)
    for key, maximum in MAXIMA.items():
        item = value[key]
        if key in ('ackSeconds', 'partialSeconds'):
            require(type(item) in (int, float) and math.isfinite(item) and 0 < item <= maximum)
        else:
            integer(item, 1, maximum)
    require(value['requestBytes'] <= value['artifactBytes'])
    return dict(value)


def reference(value, caps):
    """Validate a content reference; it deliberately contains no user path."""
    fields(value, REF_KEYS)
    require(value['schemaId'] == PREFIX + 'artifact-ref@1')
    hashValue(value['sha256'])
    integer(value['bytes'], 0, caps['artifactBytes'])
    require(value['mediaType'] in ('application/json', 'application/octet-stream'))
    require(value['encoding'] in ('raw', 'utf-8'))


def manifest(value):
    """Validate immutable archive identity and process-death durability declaration."""
    fields(value, ('schemaId', 'archiveId', 'suiteId', 'createdTimeNs', 'definitionSha256', 'format', 'durability', 'limits'))
    require(value['schemaId'] == PREFIX + 'archive@1')
    identifier(value['archiveId'], opaque=True)
    identifier(value['suiteId'], opaque=True)
    require(value['archiveId'] != value['suiteId'])
    integer(value['createdTimeNs'])
    hashValue(value['definitionSha256'])
    require(value['format'] == 'data-commit-json-lines@1')
    require(value['durability'] == 'local-process-death-os-running')
    limits(value['limits'])


class Grammar:
    """Typed ordered diagnostic state, never authority to resume or repair work."""

    def __init__(self, header):
        """Start an empty committed-prefix projection from a validated manifest."""
        manifest(header)
        self.header, self.caps = header, header['limits']
        self.planned, self.units, self.terminals = {}, {}, {}
        self.unitStatuses = {}
        self.unitObservations = {}
        self.captureRequired = False
        self.suiteOutcome = self.seal = None
        self.started = False
        self.activeUnit = None
        self.nextUnit = 0
        self.turns = {}

    def apply(self, data, read):
        """Publish diagnostic state only after the entire candidate record validates."""
        candidate = copy.deepcopy(self)
        candidate._apply(data, read)
        self.__dict__.update(candidate.__dict__)

    def _apply(self, data, read):
        """Validate one complete DATA before accepting its matching commit.

        read validates referenced immutable blobs and returns exact bytes. All
        referenced blobs are checked even if an inspection omits their content.
        """
        fields(data, DATA_KEYS)
        require(data['schemaId'] == PREFIX + 'archive-record@1' and self.seal is None)
        require(data['archiveId'] == self.header['archiveId'] and data['suiteId'] == self.header['suiteId'])
        integer(data['observedTimeNs'])
        kind, payload = data['kind'], data['payload']
        require(type(kind) is str and kind in PAYLOADS)
        fields(payload, PAYLOADS[kind])
        require(type(data['artifacts']) is list and len(data['artifacts']) <= 4)
        declared = {}
        for ref in data['artifacts']:
            reference(ref, self.caps)
            require(ref['sha256'] not in declared)
            declared[ref['sha256']] = ref
            read(ref)
        used = set()
        def blob(ref):
            """Require every payload reference to have one identical declared ref."""
            reference(ref, self.caps)
            require(declared.get(ref['sha256']) == ref)
            used.add(ref['sha256'])
            return read(ref)
        def document(ref, schemaId, keys):
            """Decode one bounded explicitly versioned JSON artifact."""
            require(ref['mediaType'] == 'application/json' and ref['encoding'] == 'utf-8')
            value = decode(blob(ref))
            fields(value, ('schemaId', *keys))
            require(value['schemaId'] == PREFIX + schemaId)
            return value
        unitId, turnId = data['unitId'], data['turnId']
        if kind == 'suite-planned':
            require(not self.started and unitId is None and turnId is None)
            raw = blob(payload['definitionRef'])
            require(digest(raw) == self.header['definitionSha256'])
            definition = decode(raw)
            require(type(definition) is dict and definition.get('schemaId') == PREFIX + 'definition@2')
            plan = document(payload['planRef'], 'plan@1', ('units',))
            require(type(plan['units']) is list and 1 <= len(plan['units']) <= 32)
            for index, unit in enumerate(plan['units']):
                fields(unit, ('unitId', 'configurationIndex', 'repetition', 'profileAlias', 'turnIds'))
                identifier(unit['unitId'], opaque=True)
                require(unit['unitId'] not in self.planned)
                integer(unit['configurationIndex'], 1, 8)
                integer(unit['repetition'], 1, 10)
                identifier(unit['profileAlias'])
                require(type(unit['turnIds']) is list and 2 <= len(unit['turnIds']) <= 16)
                for ident in unit['turnIds']:
                    identifier(ident)
                require(len(set(unit['turnIds'])) == len(unit['turnIds']))
                self.planned[unit['unitId']] = unit
            provenance = document(payload['initialProvenanceRef'], 'provenance@1', ('facts',))
            require(type(provenance['facts']) is dict)
            self.captureRequired = provenance['facts'].get('capturePolicy') == 'required'
            policy = provenance['facts'].get('validationPolicy')
            require(type(policy) is dict)
            schema = importlib.import_module('first-party.applications.experimentRunner.definition')
            schema.validate(definition, policy)
            expected = [(i + 1, repetition, config['profile']) for i, config in enumerate(definition['configurations'])
                for repetition in range(1, definition['repetitions'] + 1)]
            require(len(expected) == len(self.planned))
            for unit, expectedUnit in zip(self.planned.values(), expected, strict=True):
                require((unit['configurationIndex'], unit['repetition'], unit['profileAlias']) == expectedUnit)
                require(unit['turnIds'] == [turn['id'] for turn in definition['conversation']['turns']])
            self.started = True
        else:
            require(self.started)
            if unitId is not None:
                require(unitId in self.planned and self.suiteOutcome is None)
            if kind == 'unit-planned':
                require(turnId is None and self.activeUnit is None and unitId not in self.units)
                require(self.nextUnit < len(self.planned) and list(self.planned)[self.nextUnit] == unitId)
                planned = self.planned[unitId]
                require(all(payload[k] == planned[k] for k in ('configurationIndex', 'repetition', 'profileAlias')))
                if payload['jobId'] is not None:
                    identifier(payload['jobId'])
                require(type(decode(blob(payload['requestedOptionsRef']))) is dict)
                document(payload['provenanceRef'], 'provenance@1', ('facts',))
                self.units[unitId] = dict(turnIndex=0, active=None)
                self.activeUnit = unitId
                self.nextUnit += 1
            elif kind == 'unit-outcome':
                require(unitId == self.activeUnit and turnId is None)
                value = document(payload['resultRef'], 'unit-summary@1',
                    ('unitId', 'status', 'jobOutcome', 'lifetimeBoundary', 'reservation', 'configurationEvidence', 'turnResults', 'unstarted'))
                require(value['unitId'] == unitId and value['status'] in ('succeeded', 'failed', 'cancelled'))
                require(value['jobOutcome'] in (None, 'Succeeded', 'Failed', 'Cancelled', 'Pending', 'Running'))
                require(value['lifetimeBoundary'] in ('not-started', 'completed', 'failed-or-unknown'))
                require(type(value['reservation']) is dict and type(value['turnResults']) is list and type(value['unstarted']) is list)
                require(value['configurationEvidence'] is None or type(value['configurationEvidence']) is dict)
                expected = [t['resultRef'] for (u, _), t in self.turns.items() if u == unitId and 'resultRef' in t]
                require(value['turnResults'] == expected)
                require(value['unstarted'] == [t for t in self.planned[unitId]['turnIds'] if (unitId, t) not in self.turns])
                if value['status'] == 'succeeded':
                    require(value['jobOutcome'] == 'Succeeded' and value['lifetimeBoundary'] == 'completed')
                    require(len(expected) == len(self.planned[unitId]['turnIds']))
                    require(all(self.turns[unitId, t].get('resultStatus') == 'succeeded' for t in self.planned[unitId]['turnIds']))
                self.terminals[unitId] = payload['resultRef']
                self.unitStatuses[unitId] = value['status']
                self.unitObservations[unitId] = dict(jobOutcome=value['jobOutcome'], lifetimeBoundary=value['lifetimeBoundary'],
                    reservation=value['reservation'])
                self.activeUnit = None
            elif kind in ('turn-planned', 'request-evidence', 'partial', 'turn-completed'):
                require(unitId == self.activeUnit and unitId in self.units)
                state = self.units[unitId]
                if kind == 'turn-planned':
                    require(state['active'] is None and state['turnIndex'] < len(self.planned[unitId]['turnIds']))
                    require(self.planned[unitId]['turnIds'][state['turnIndex']] == turnId)
                    identifier(payload['attemptId'], opaque=True)
                    query = decode(blob(payload['queryRef']))
                    fields(query, ('formatId', 'messages'))
                    require(query['formatId'] == 'actant.llm.messages@1' and type(query['messages']) is list)
                    require(1 <= len(query['messages']) <= 31 and len(query['messages']) % 2 == 1)
                    for index, message in enumerate(query['messages']):
                        fields(message, ('role', 'content'))
                        require(message['role'] == ('user' if index % 2 == 0 else 'assistant') and type(message['content']) is str)
                    require(type(decode(blob(payload['requestedOptionsRef']))) is dict)
                    self.turns[unitId, turnId] = dict(attemptId=payload['attemptId'], phase=None,
                        answerOffset=0, reasoningOffset=0, eventTo=0, partials=0, providerCallId=None)
                    state['active'] = turnId
                    state['turnIndex'] += 1
                else:
                    require(state['active'] == turnId)
                    turn = self.turns[unitId, turnId]
                    if kind == 'request-evidence':
                        require(payload['attemptId'] == turn['attemptId'])
                        identifier(payload['providerCallId'], opaque=True)
                        require(turn['providerCallId'] in (None, payload['providerCallId']))
                        turn['providerCallId'] = payload['providerCallId']
                        phase = payload['phase']
                        allowed = {None: ('serialized', 'final'), 'serialized': ('transport-attempt', 'final'),
                            'transport-attempt': ('response-headers-observed', 'final'), 'response-headers-observed': ('final',)}
                        require(phase in allowed.get(turn['phase'], ()))
                        require(payload['serverConsumption'] == 'unknown')
                        for key in ('mayHaveSent', 'responseHeadersObserved', 'responseBodyObserved'):
                            require(payload[key] is None or type(payload[key]) is bool)
                        if phase == 'serialized':
                            raw = blob(payload['bodyRef'])
                            integer(payload['length'], 0, self.caps['requestBytes'])
                            hashValue(payload['sha256'])
                            require(len(raw) <= self.caps['requestBytes'] and len(raw) == payload['length'] and digest(raw) == payload['sha256'])
                        else:
                            require(payload['bodyRef'] is None and payload['length'] is None and payload['sha256'] is None)
                        if phase in ('serialized', 'transport-attempt'):
                            require(all(payload[k] is None for k in ('mayHaveSent', 'responseHeadersObserved', 'responseBodyObserved')))
                        elif phase == 'response-headers-observed':
                            require(payload['mayHaveSent'] is None and payload['responseHeadersObserved'] is True and payload['responseBodyObserved'] is None)
                        else:
                            require(type(payload['mayHaveSent']) is bool)
                            require(payload['responseHeadersObserved'] in (None, True) and payload['responseBodyObserved'] in (None, True))
                            require(payload['responseBodyObserved'] is not True or payload['responseHeadersObserved'] is True)
                        turn['phase'] = phase
                    elif kind == 'partial':
                        require(payload['partial'] is True and payload['attemptId'] == turn['attemptId'])
                        integer(payload['eventFrom'], turn['eventTo'] + 1, self.caps['turnEvents'])
                        integer(payload['eventTo'], payload['eventFrom'], self.caps['turnEvents'])
                        require(payload['answerOffset'] == turn['answerOffset'] and payload['reasoningOffset'] == turn['reasoningOffset'])
                        value = document(payload['deltaRef'], 'partial@1', ('answerDelta', 'reasoningDelta', 'answerAvailable', 'reasoningAvailable'))
                        for field, offset, availability in (('answerDelta', 'answerOffset', 'answerAvailable'),
                                ('reasoningDelta', 'reasoningOffset', 'reasoningAvailable')):
                            require(type(value[availability]) is bool and (value[field] is None or type(value[field]) is str))
                            require(value[availability] == (value[field] is not None))
                            turn[offset] += len(value[field] or '')
                        turn['eventTo'] = payload['eventTo']
                        turn['partials'] += 1
                        require(turn['partials'] <= self.caps['partialRecords'])
                    else:
                        value = document(payload['resultRef'], 'turn-result@1', ('result',))['result']
                        fields(value, TURN_KEYS)
                        require(value['turnId'] == turnId and value['status'] in ('succeeded', 'failed', 'cancelled'))
                        if value['status'] == 'succeeded':
                            require(type(value['answer']) is str and value['partial'] is None)
                            require(not self.captureRequired or turn['phase'] == 'final')
                        require(value['reasoning'] is None or type(value['reasoning']) is str)
                        require(type(value['input']) is str and type(value['cleanup']) is dict)
                        for name in ('startedTimeNs', 'endedTimeNs', 'durationNs'):
                            integer(value[name])
                        turn['resultRef'] = payload['resultRef']
                        turn['resultStatus'] = value['status']
                        turn['cleanup'] = value['cleanup']
                        state['active'] = None
            elif kind == 'suite-outcome':
                require(unitId is None and turnId is None and self.activeUnit is None and self.suiteOutcome is None)
                value = document(payload['summaryRef'], 'suite-summary@1', ('status', 'unitResults', 'unstarted', 'startedTimeNs', 'endedTimeNs', 'durationNs'))
                require(value['status'] in ('succeeded', 'failed', 'cancelled'))
                require(value['unitResults'] == list(self.terminals.values()))
                require(value['unstarted'] == [u for u in self.planned if u not in self.units])
                if value['status'] == 'succeeded':
                    require(len(self.terminals) == len(self.planned))
                    require(all(s == 'succeeded' for s in self.unitStatuses.values()))
                self.suiteOutcome = value['status']
            elif kind == 'cleanup-observed':
                require(unitId is None and turnId is None)
                require(payload['component'] in ('signal', 'host', 'temporary'))
                require(payload['observedOutcome'] in ('completed', 'failed-or-unknown', 'unavailable'))
                require(type(decode(blob(payload['evidenceRef']))) is dict)
            elif kind == 'archive-seal':
                require(unitId is None and turnId is None and self.suiteOutcome is not None)
                require(payload['lastObservedSuiteStatus'] == self.suiteOutcome and payload['finalizationObserved'] == 'not-yet-closed')
                integer(payload['terminalSequence'], 1, data['sequence'] - 1)
                self.seal = data['sequence']
        require(used == set(declared))
