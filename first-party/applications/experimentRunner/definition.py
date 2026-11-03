# file: first-party/applications/experimentRunner/definition.py ; version: 1
"""Strict bootstrap experiment schema and immutable canonical JSON snapshots."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

MAX_BYTES = 1024 * 1024
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
    """Copy and validate the entire v1 definition against independently trusted caps."""
    try:
        value = decode(canonical(value))
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as error:
        raise ValidationError('Invalid definition JSON.') from error
    fields(value, ('schemaId', 'experimentId', 'configurations', 'conversation', 'repetitions', 'errorPolicy', 'contextPolicy'))
    require(value['schemaId'] == 'actant.experiment.definition@1', 'Unsupported definition schema.')
    name(value['experimentId'])
    require(type(value['repetitions']) is int and value['repetitions'] == 1, 'Only one repetition is supported.')
    require(value['errorPolicy'] == 'stop' and value['contextPolicy'] == 'fresh-best-effort', 'Unsupported run policy.')
    configurations = value['configurations']
    require(type(configurations) is list and len(configurations) == 1, 'Exactly one configuration is supported.')
    config = configurations[0]
    fields(config, ('name', 'profile', 'defaults', 'allowedOverrides'))
    name(config['name'])
    name(config['profile'])
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
        require(set(overrides) <= set(allowed), 'Turn override is not allowed.')
        merged = config['defaults'] | overrides
        options(merged, complete=True)
        require(merged['maxTokens'] <= policy['maxTokens'] and merged['timeoutSeconds'] <= policy['timeoutSeconds'],
                'Request exceeds host limits.')
    return Snapshot(canonical(value))
