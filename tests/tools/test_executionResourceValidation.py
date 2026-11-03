# file: tests/tools/test_executionResourceValidation.py ; version: 4
"""ER-04 validation contracts: bounded metadata, private evidence and real facade composition."""
import json
import struct
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.executionResourceValidation import preflight
from tools.executionResourceValidation.run import Recorder, ResponseEvidence, Validation, execute, load_adapter, unavailable_signals, transport_facts
from backend.telemetry import SignalUnavailable, GpuObservation
from tests.first_party.llmDrivers.test_llamaCppExecutionResources import rig


def encoded(text):
    """Encode one GGUF UTF-8 string without any model weights."""
    data = text.encode()
    return struct.pack('<Q', len(data)) + data


def gguf(path, *, version=3, metadata=None):
    """Write a synthetic metadata header followed by non-metadata sentinel bytes."""
    items = metadata or [('general.architecture', 8, encoded('qwen3')),
                         ('tokenizer.chat_template', 8, encoded('PRIVATE_TEMPLATE enable_thinking')),
                         ('qwen3.context_length', 4, struct.pack('<I', 4096)),
                         ('tokenizer.ggml.tokens', 9, struct.pack('<IQ', 8, 2) + encoded('a') + encoded('b'))]
    data = b'GGUF' + struct.pack('<IQQ', version, 123, len(items))
    data += b''.join(encoded(key) + struct.pack('<I', kind) + value for key, kind, value in items)
    path.write_bytes(data + b'NO_TENSOR_READ_REQUIRED')
    return path


def local_config(tmp_path):
    """Supply explicit synthetic local files, never machine-specific asset defaults."""
    binary = tmp_path / 'server.exe'
    binary.touch()
    model = gguf(tmp_path / 'model.gguf')
    return {'executable': str(binary), 'models': {'nanbeige': str(model), 'qwen': str(model)}}


def test_gguf_inspects_only_bounded_metadata(tmp_path):
    """Tensor count and template identity come from metadata, without tensor parsing."""
    path = gguf(tmp_path / 'model.gguf')
    value = preflight.gguf_metadata(path)
    assert value['tensors'] == 123
    assert value['selected']['general.architecture'] == 'qwen3'
    assert value['selected']['qwen3.context_length'] == 4096
    assert 'tokenizer.ggml.tokens' not in value['selected']
    assert 'PRIVATE_TEMPLATE' in value['selected']['tokenizer.chat_template']


@pytest.mark.parametrize('data', [b'wrong', b'GGUF' + struct.pack('<IQQ', 1, 0, 0),
                               b'GGUF' + struct.pack('<IQQ', 3, 0, 99999),
                               b'GGUF' + struct.pack('<IQQQ', 3, 0, 1, 99999999)])
def test_gguf_rejects_invalid_or_unbounded_input(tmp_path, data):
    """Malformed metadata never becomes apparent preflight success."""
    path = tmp_path / 'bad.gguf'
    path.write_bytes(data)
    with pytest.raises((preflight.PreflightError, UnicodeError)):
        preflight.gguf_metadata(path)


@pytest.mark.parametrize('field,value', [('maxTokens', 0), ('maxTokens', 129), ('threads', True),
                                      ('contextTokens', 100000), ('prompt', ''), ('extraArgs', ['--anything'])])
def test_local_configuration_enforces_run_scope(tmp_path, field, value):
    """No arbitrary startup flags, unbounded contexts or implicit local asset fallback."""
    config = local_config(tmp_path)
    config[field] = value
    path = tmp_path / 'local.json'
    path.write_text(json.dumps(config))
    with pytest.raises(preflight.PreflightError):
        preflight.load_config(path)


def test_preflight_missing_asset_is_explicit(tmp_path):
    """Unavailable supplied assets reject rather than triggering model downloads."""
    config = local_config(tmp_path)
    config['models']['qwen'] = str(tmp_path / 'missing.gguf')
    path = tmp_path / 'local.json'
    path.write_text(json.dumps(config))
    with pytest.raises(preflight.PreflightError, match='unavailable'):
        preflight.load_config(path)


def test_preflight_public_summary_excludes_private_paths_and_templates(tmp_path, monkeypatch):
    """Only a deliberate bounded public projection leaves local asset/private records."""
    secret = 'PRIVATE_USER_SENTINEL'
    assets = {'executable': {'path': secret, 'sha256': 'abc', 'version': secret},
              'models': {'x': {'gguf': {'template': secret}}}, 'help': secret}
    monkeypatch.setattr('tools.executionResourceValidation.run.inspect_assets', lambda config: assets)
    summary, owner = execute({'prompt': secret}, tmp_path, 'preflight')
    assert owner is None and summary['phases']['04A']['status'] == 'verified'
    assert secret not in json.dumps(summary)
    assert secret in (tmp_path / 'assets.private.json').read_text()


def test_response_capture_preserves_bytes_and_bounds_private_retention():
    """Recording cannot change provider bytes or retain unbounded generated output."""
    body = b'x' * 200000
    response = BytesIO(body)
    record = {'response': [], 'truncated': False}
    proxy = ResponseEvidence(response, record)
    assert proxy.read() == body
    assert len(''.join(record['response'])) == 131072 and record['truncated']
    proxy.close()
    assert response.closed


def test_core_harness_uses_real_host_grants_and_neutral_facades(rig, tmp_path, monkeypatch):
    """The reusable core scenarios pass with actual ER-01/02/03 and fake OS/HTTP only."""
    _, _, _, processes, _, state, _, _, source_config = rig
    state['slots'] = [{'id': 0, 'is_processing': False}]
    model = source_config['models']['A']['modelPath']
    config = {'models': {'nanbeige': model, 'qwen': model}, 'executable': source_config['executable'],
              'contextTokens': 1024, 'maxTokens': 12, 'threads': 4, 'timeoutSeconds': 10,
              'startupSeconds': 10, 'prompt': 'private local prompt', 'reasoningPrompt': 'private arithmetic'}
    recorder = SimpleNamespace(base_url='http://127.0.0.1:28891', processes=processes)
    adapter = load_adapter()
    def advertised(self, model):
        """Replace only the model-free help probe; fake subprocesses cannot run a binary."""
        return {'status': 'advertised-pinned-surface'}

    monkeypatch.setattr(adapter._impl.LlamaCppDriver, 'checkCapabilities', advertised)
    validation = Validation(config, tmp_path, recorder, adapter)
    try:
        validation.ordinary()
        validation.safety()
        reasoning = validation.reasoning()
        assert len(reasoning) == 4 and all(item['serverAccepted'] for item in reasoning)
        assert all(case['passed'] for case in validation.checks)
        assert any(case['case'] == 'cancel-before-send' for case in validation.checks)
    finally:
        assert validation.close()
    assert all(process.poll() is not None for process in processes)


def test_unknown_cleanup_reports_blocked_without_process_recovery():
    """Harness closure preserves unknown state instead of calling terminate/kill."""
    def fail():
        """Represent an explicitly unavailable provider cleanup disposition."""
        raise RuntimeError('private exception text')

    validation = object.__new__(Validation)
    validation.host = SimpleNamespace(stop=fail, executionResources=SimpleNamespace(snapshots=()))
    validation.private = []
    assert validation.close() is False
    assert validation.private == [{'cleanupErrorType': 'RuntimeError', 'snapshots': ()}]


def test_recorder_does_not_redirect_capability_probe_output(tmp_path):
    """Model-free help/version pipes must remain visible to the production LC checker."""
    calls = []
    def popen(args, **kwargs):
        """Record test-owned launch options without creating a process."""
        calls.append(kwargs)
        return SimpleNamespace(pid=42)

    adapter = SimpleNamespace(_impl=SimpleNamespace(subprocess=SimpleNamespace(Popen=popen)),
                              http=SimpleNamespace(urlopen=None))
    binary = tmp_path / 'server.exe'
    with Recorder(adapter, binary, 'http://127.0.0.1:1', tmp_path) as recorder:
        adapter._impl.subprocess.Popen([str(binary), '--help'], stdout=-1, stderr=-1)
        assert calls[-1] == {'stdout': -1, 'stderr': -1}
        assert not recorder.processes
        adapter._impl.subprocess.Popen([str(binary), '-m', 'local.gguf'])
        assert calls[-1]['stdout'] is recorder.log
        assert len(recorder.processes) == 1


def test_missing_nested_sensors_are_not_reported_as_real_zero():
    """A valid zero utilization differs from unavailable GPU submetrics."""
    missing = SignalUnavailable('not installed')
    sample = GpuObservation(0, 'private name', 100, 40, 60, 0.0, 0.0,
        missing, missing, missing, missing, missing, missing, missing)
    names = unavailable_signals(sample, 'gpu')
    assert 'gpu.temperatureCelsius' in names and 'gpu.fanSpeedPercent' in names
    assert 'gpu.gpuUtilizationPercent' not in names


def test_wire_projection_preserves_control_facts_without_private_content():
    """Protocol provenance must not disclose completion IDs, prompts or reasoning."""
    secret = 'PRIVATE_WIRE_SENTINEL'
    request = {'messages': [{'content': secret}], 'id_slot': 0, 'cache_prompt': False,
               'reasoning_effort': 'none', 'chat_template_kwargs': {'enable_thinking': False}}
    chunk = {'id': secret, 'choices': [{'delta': {'content': secret}, 'finish_reason': 'stop'}]}
    records = [{'endpoint': '/v1/chat/completions', 'request': json.dumps(request),
                'response': ['data: ' + json.dumps(chunk) + '\n', 'data: [DONE]\n']},
               {'endpoint': '/slots'}]
    result = transport_facts(records)
    assert secret not in json.dumps(result)
    assert result['streamCalls'][0] == {'ownedSlotRequested': True, 'cacheReuseDisabled': True,
        'nativeDone': True, 'nativeIdConsistent': True, 'finishReasons': ['stop'],
        'enableThinkingForwarded': False, 'reasoningEffortForwarded': 'none'}
    assert result['postTerminalSlotReads'] == 1
