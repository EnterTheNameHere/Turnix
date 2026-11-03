# file: first-party/applications/experimentRunner/definition.py ; version: 2
"""Strict bootstrap experiment schema and immutable canonical JSON snapshots."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

MAX_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 64 * 1024 * 1024
OPTIONS = frozenset(('temperature', 'maxTokens', 'timeoutSeconds', 'topP', 'minP',
                     'topK', 'repeatPenalty', 'seed', 'reasoningFormat', 'reasoningEffort', 'chatTemplateKwargs'))


class ValidationError(ValueError):
    """Reject declarative input without echoing private values into diagnostics."""


def require(condition, message):
    """Raise a fixed schema diagnostic when an invariant does not hold."""
    if not condition:
        raise ValidationError(message)


def fields(value, required, optional=()):
    """Require exact JSON object keys, rejecting every undeclared control."""
    require(type(value) is dict and set(required) <= value.keys()
            and not value.keys() - set(required) - set(optional), 'Invalid object fields.')


def name(value):
    """Validate bounded exact identifiers without normalizing their contents."""
    require(type(value) is str and bool(value.strip()) and len(value) <= 128, 'Invalid identifier.')


def number(value, low, high, *, integer=False, exclusive=False):
    """Reject boolean, null and nonfinite controls before checking inclusive bounds."""
    require(type(value) in ((int,) if integer else (int, float)), 'Invalid numeric type.')
    require((type(value) is int or math.isfinite(value)) and (value > low if exclusive else value >= low)
            and value <= high, 'Numeric control outside bounds.')


def options(value, *, complete=False):
    """Validate bounded request controls and conflicts on every merged turn."""
    fields(value, (), OPTIONS)
    if complete:
        require({'maxTokens', 'timeoutSeconds'} <= value.keys(), 'Missing required defaults.')
    for key, item in value.items():
        if key in ('temperature', 'topP', 'minP'):
            number(item, 0, 2 if key == 'temperature' else 1)
        elif key in ('maxTokens', 'topK', 'seed'):
            low, high = {'maxTokens': (1, 4096), 'topK': (1, 100000), 'seed': (-2147483648, 2147483647)}[key]
            number(item, low, high, integer=True)
        elif key in ('timeoutSeconds', 'repeatPenalty'):
            number(item, 0, 120 if key == 'timeoutSeconds' else 10, exclusive=True)
        elif key == 'reasoningFormat':
            require(type(item) is str and item in ('none', 'auto', 'deepseek', 'deepseek-legacy'), 'Invalid reasoning format.')
        elif key == 'reasoningEffort':
            name(item)
        else:
            fields(item, (), ('enable_thinking', 'preserve_reasoning'))
            require(all(type(flag) is bool for flag in item.values()), 'Invalid template flags.')
    require(not (value.get('reasoningEffort') == 'none'
                 and value.get('chatTemplateKwargs', {}).get('enable_thinking') is True), 'Conflicting reasoning controls.')


def canonical(value):
    """Encode sorted-key compact UTF-8 JSON, preserving strings and rejecting NaN."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def uniqueObject(pairs):
    """Reject duplicate keys at every JSON object depth."""
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key.')
        result[key] = value
    return result


def rejectConstant(value):
    """Reject nonstandard JSON constants without retaining their input text."""
    raise ValidationError('Nonfinite JSON constant.')


def finiteFloat(value):
    """Reject exponent overflow as well as explicitly spelled nonfinite constants."""
    result = float(value)
    require(math.isfinite(result), 'Nonfinite JSON number.')
    return result


def decode(data):
    """Parse bounded strict UTF-8 JSON without duplicate keys or nonfinite numbers."""
    require(type(data) is bytes and len(data) <= MAX_BYTES, 'JSON exceeds byte limit.')
    try:
        return json.loads(data.decode('utf-8', errors='strict'), object_pairs_hook=uniqueObject,
                          parse_constant=rejectConstant, parse_float=finiteFloat)
    except (UnicodeError, ValueError, RecursionError, OverflowError) as error:
        raise ValidationError('Invalid strict JSON.') from error


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Own immutable JSON bytes; every inspection returns a detached mutable copy."""

    data: bytes

    def value(self):
        """Return a new plain JSON tree, never an alias to recorder state."""
        return json.loads(self.data)

    @property
    def sha256(self):
        """Identify the exact canonical snapshot bytes, independently of run identity."""
        return hashlib.sha256(self.data).hexdigest()


def validate(value, policy):
    """Freeze a v2 suite only after every configuration and expansion budget validates."""
    try:
        value = decode(canonical(value))
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as error:
        raise ValidationError('Invalid definition JSON.') from error
    fields(value, ('schemaId', 'experimentId', 'configurations', 'conversation', 'repetitions', 'errorPolicy', 'contextPolicy'))
    require(value['schemaId'] == 'actant.experiment.definition@2', 'Unsupported definition schema; use version 2.')
    name(value['experimentId'])
    number(value['repetitions'], 1, min(10, policy['maxRepetitions']), integer=True)
    require(value['errorPolicy'] == 'stop' and value['contextPolicy'] == 'fresh-best-effort', 'Unsupported run policy.')
    configurations = value['configurations']
    require(type(configurations) is list and 1 <= len(configurations) <= min(8, policy['maxConfigurations']), 'Invalid configuration count.')
    executions = len(configurations) * value['repetitions']
    require(executions <= min(32, policy['maxExecutions']), 'Too many conversation executions.')
    names = set()
    for config in configurations:
        fields(config, ('name', 'profile', 'defaults', 'allowedOverrides'))
        name(config['name'])
        name(config['profile'])
        require(config['name'] not in names, 'Duplicate configuration name.')
        names.add(config['name'])
        require(config['profile'] in policy['aliases'], 'Profile alias is not host approved.')
        options(config['defaults'], complete=True)
        config['defaults'].setdefault('temperature', 0.7)
        allowed = config['allowedOverrides']
        require(type(allowed) is list and all(type(key) is str for key in allowed), 'Invalid override allowlist.')
        require(len(set(allowed)) == len(allowed) and set(allowed) <= OPTIONS, 'Invalid override allowlist.')
    conversation = value['conversation']
    fields(conversation, ('id', 'historyPolicy', 'turns'))
    name(conversation['id'])
    require(conversation['historyPolicy'] == 'replay-answers', 'Unsupported history policy.')
    turns = conversation['turns']
    require(type(turns) is list and 2 <= len(turns) <= min(16, policy['maxTurns']), 'Invalid turn count.')
    require(executions * len(turns) <= min(256, policy['maxTurnCalls']), 'Too many requested turn calls.')
    ids = set()
    for turn in turns:
        fields(turn, ('id', 'role', 'content'), ('overrides',))
        name(turn['id'])
        require(turn['id'] not in ids, 'Duplicate turn identifier.')
        ids.add(turn['id'])
        require(turn['role'] == 'user', 'Only user input turns are supported.')
        require(type(turn['content']) is str and 1 <= len(turn['content']) <= 16384, 'Invalid turn content.')
        overrides = turn.get('overrides', {})
        options(overrides)
        for config in configurations:
            require(set(overrides) <= set(config['allowedOverrides']), 'Turn override is not allowed.')
            merged = config['defaults'] | overrides
            options(merged, complete=True)
            require(merged['maxTokens'] <= policy['maxTokens'] and merged['timeoutSeconds'] <= policy['timeoutSeconds'],
                    'Request exceeds host limits.')
    # Bound retained JSON before allocating units, including worst-case escaped
    # provider output repeated through history, metadata and private host evidence.
    prefixBytes, conversationBudget = 0, 0
    for index, turn in enumerate(turns):
        prefixBytes += len(canonical(turn['content']))
        conversationBudget += prefixBytes + (index + 3) * 6 * policy['maxOutputCharacters'] + 65536
    definitionBytes = len(canonical(value))
    require(definitionBytes + executions * (definitionBytes + conversationBudget + 262144) <= MAX_RESULT_BYTES,
            'Expanded private result exceeds retained-data budget.')
    return Snapshot(canonical(value))


def unitDefinition(definition, configurationIndex):
    """Derive a detached single-unit definition without changing the suite snapshot."""
    value = definition.value()
    value['configurations'] = [value['configurations'][configurationIndex]]
    value['repetitions'] = 1
    return Snapshot(canonical(value))
