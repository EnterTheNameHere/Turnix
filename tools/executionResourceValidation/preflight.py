# file: tools/executionResourceValidation/preflight.py ; version: 1
"""Bounded, model-free local asset inspection for explicit ER-04 validation."""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
from pathlib import Path


class PreflightError(ValueError):
    """Reject unavailable assets or malformed local configuration before startup."""


def digest(path):
    """Hash existing bytes in bounded memory without copying any model asset."""
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def gguf_metadata(path):
    """Inspect GGUF v2/v3 metadata only, bounding sizes/depth and skipping tensor bytes."""
    formats = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f', 7: '?', 10: 'Q', 11: 'q', 12: 'd'}
    with Path(path).open('rb') as source:
        def number(format):
            """Require complete little-endian scalar bytes within the metadata budget."""
            count = struct.calcsize('<' + format)
            value = source.read(count)
            if len(value) != count or source.tell() > 64 * 1024 * 1024:
                raise PreflightError('Truncated or oversized GGUF metadata.')
            return struct.unpack('<' + format, value)[0]

        def string(keep=True):
            """Read a bounded metadata string or skip its exact declared byte length."""
            length = number('Q')
            if length > 8 * 1024 * 1024:
                raise PreflightError('Oversized GGUF string.')
            data = source.read(length)
            if len(data) != length:
                raise PreflightError('Truncated GGUF string.')
            return data.decode('utf-8', errors='strict') if keep else None

        def value(kind, keep, depth=0):
            """Skip unknown metadata arrays without retaining tokenizer vocabularies."""
            if depth > 4:
                raise PreflightError('GGUF array nesting exceeds validation bound.')
            if kind in formats:
                item = number(formats[kind])
                return item if keep else None
            if kind == 8:
                return string(keep)
            if kind == 9:
                child, count = number('I'), number('Q')
                if count > 1_000_000:
                    raise PreflightError('GGUF array exceeds validation bound.')
                for _ in range(count):
                    value(child, False, depth + 1)
                return {'items': count} if keep else None
            raise PreflightError('Unknown GGUF metadata type.')

        if source.read(4) != b'GGUF':
            raise PreflightError('Expected a GGUF file.')
        version, tensors, count = number('I'), number('Q'), number('Q')
        if version not in (2, 3) or count > 4096:
            raise PreflightError('Unsupported GGUF version or metadata count.')
        selected = {}
        for _ in range(count):
            key, kind = string(), number('I')
            keep = key in ('general.name', 'general.architecture', 'general.file_type', 'tokenizer.chat_template') or key.endswith('.context_length')
            item = value(kind, keep)
            if keep:
                selected[key] = item
        return {'version': version, 'tensors': tensors, 'metadataCount': count, 'selected': selected}


def load_config(path):
    """Read explicitly supplied local assets and enforce conservative finite run bounds."""
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    allowed = {'executable', 'models', 'contextTokens', 'maxTokens', 'threads', 'timeoutSeconds',
               'startupSeconds', 'prompt', 'reasoningPrompt', 'playwright', 'browser', 'hashModels'}
    if type(value) is not dict or set(value) - allowed:
        raise PreflightError('Unknown local configuration fields.')
    if type(value.get('models')) is not dict or set(value['models']) != {'nanbeige', 'qwen'}:
        raise PreflightError('Explicit nanbeige and qwen model paths are required.')
    for name, default, low, high in [('contextTokens', 1024, 256, 4096), ('maxTokens', 24, 1, 128),
                                   ('threads', 4, 1, 8), ('timeoutSeconds', 90, 5, 180), ('startupSeconds', 90, 5, 180)]:
        selected = value.setdefault(name, default)
        if type(selected) is not int or not low <= selected <= high:
            raise PreflightError('Local run bound outside permitted range: ' + name)
    value.setdefault('prompt', 'Reply with the word Ready.')
    value.setdefault('reasoningPrompt', 'What is 17 multiplied by 19? Give a short answer.')
    for key in ('prompt', 'reasoningPrompt'):
        if type(value[key]) is not str or not 1 <= len(value[key]) <= 4096:
            raise PreflightError('Prompts must be bounded local strings.')
    if type(value.setdefault('hashModels', True)) is not bool:
        raise PreflightError('hashModels must be a boolean.')
    for raw in (value.get('executable'), *value['models'].values()):
        if type(raw) is not str or not Path(raw).is_file():
            raise PreflightError('A supplied local asset is unavailable.')
    return value


def inspect_assets(config):
    """Run only model-free help/version and identify supplied local files without fallback."""
    executable = str(Path(config['executable']).resolve())
    probes = {}
    for flag in ('--version', '--help'):
        process = subprocess.run([executable, flag], capture_output=True, timeout=20,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if process.returncode:
            raise PreflightError('A model-free binary probe failed.')
        probes[flag] = (process.stdout + process.stderr)[:1024 * 1024].decode('utf-8', errors='replace')
    required = ('--slots', '--parallel', '--reasoning', '--reasoning-format', '--spec-type')
    missing = [flag for flag in required if flag not in probes['--help']]
    if missing or '060b2fea4' not in probes['--version']:
        raise PreflightError('Supplied binary does not advertise the pinned validation surface.')
    models = {}
    for name, raw in config['models'].items():
        path = Path(raw).resolve()
        stat = path.stat()
        models[name] = {'path': str(path), 'size': stat.st_size, 'mtimeNs': stat.st_mtime_ns,
                        'sha256': digest(path) if config['hashModels'] else None, 'gguf': gguf_metadata(path)}
    return {'executable': {'path': executable, 'sha256': digest(executable), 'version': probes['--version']},
            'requiredFlags': list(required), 'help': probes['--help'], 'models': models}
