# file: tests/first_party/llmDrivers/test_llamaCpp.py ; version: 1
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.llm.llmTypes import LlmCallRequest, LlmQuery

_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "llmDrivers"
    / "llamaCpp"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("llamaCppCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
llamaCpp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(llamaCpp)


def _driver(*, defaults=None, **modelOverrides):
    """Builds an unmanaged one-model driver suitable for argument/profile tests."""
    model = {"modelPath": "model.gguf", **modelOverrides}
    config = {
        "manageServer": False,
        "baseUrl": "  http://127.0.0.1:8080/  ",
        "models": {"model-a": model},
    }
    if defaults is not None:
        config.update(defaults)
    return llamaCpp.LlamaCppDriver(config)


def _serverArgs(driver):
    """Returns server argv after installing a synthetic executable path."""
    driver.executable = Path("llama-server.exe")
    return driver._serverArgs(driver.models["model-a"])


def test_model_defaults_to_one_parallel_slot_and_server_args_emit_it():
    """Existing omitted settings preserve one slot and ordinary launch behavior."""
    driver = _driver(contextWindowTokens=8192, gpuLayers=12, threads=4)
    model = driver.models["model-a"]
    assert model.parallelSlots == 1
    assert driver.baseUrl == "http://127.0.0.1:8080"
    args = _serverArgs(driver)
    assert args[args.index("--parallel") + 1] == "1"
    assert args[args.index("-c") + 1] == "8192"
    assert "--threads-batch" not in args
    assert "--load-mode" not in args


def test_gpt_oss_profile_materializes_exact_first_class_server_controls():
    """GPT-OSS benchmark controls survive validation and become explicit argv."""
    driver = _driver(
        gpuLayers=999,
        cpuMoeLayers=31,
        threads=10,
        threadsBatch=10,
        batchSize=2048,
        ubatchSize=512,
        unifiedKv=True,
        kvOffload=True,
        cacheTypeK="q8_0",
        cacheTypeV="q8_0",
        flashAttention=True,
        loadMode="mlock",
        parallelSlots=1,
    )
    args = _serverArgs(driver)
    expected = {
        "-ngl": "999",
        "-t": "10",
        "--threads-batch": "10",
        "--batch-size": "2048",
        "--ubatch-size": "512",
        "--cache-type-k": "q8_0",
        "--cache-type-v": "q8_0",
        "--n-cpu-moe": "31",
        "--load-mode": "mlock",
        "--parallel": "1",
        "--flash-attn": "on",
    }
    for flag, value in expected.items():
        assert args[args.index(flag) + 1] == value
    assert "--kv-unified" in args
    assert "--kv-offload" in args
    assert driver.models["model-a"].gpuLayers == 999


def test_model_values_override_top_level_defaults():
    """Per-model launch values take precedence over reusable driver defaults."""
    driver = _driver(
        defaults={
            "threadsBatch": 4,
            "batchSize": 256,
            "unifiedKv": False,
            "loadMode": "mmap",
        },
        threadsBatch=10,
        batchSize=2048,
        unifiedKv=True,
        loadMode="mlock",
    )
    model = driver.models["model-a"]
    assert model.threadsBatch == 10
    assert model.batchSize == 2048
    assert model.unifiedKv is True
    assert model.loadMode == "mlock"


def test_new_model_controls_validate_exact_types_and_ranges():
    """Structured execution controls reject ambiguous or invalid configuration."""
    for key in ("threadsBatch", "batchSize", "ubatchSize"):
        with pytest.raises(ValueError, match="positive exact integer"):
            _driver(**{key: 0})
    with pytest.raises(ValueError, match="non-negative exact integer"):
        _driver(cpuMoeLayers=-1)
    for key in ("unifiedKv", "kvOffload", "flashAttention"):
        with pytest.raises(ValueError, match="boolean"):
            _driver(**{key: 1})
    with pytest.raises(ValueError, match="loadMode must be one of"):
        _driver(loadMode="legacy-mmap")
    with pytest.raises(ValueError, match="non-empty string"):
        _driver(cacheTypeK="  ")


def test_first_class_flags_are_rejected_from_extra_args():
    """Escape-hatch arguments cannot create a second authority for structured fields."""
    for flag in (
        "--parallel", "-np", "-ngl", "--threads-batch", "--batch-size",
        "--ubatch-size", "--kv-unified", "--no-kv-offload", "--cache-type-k",
        "--cache-type-v", "--flash-attn", "--n-cpu-moe", "--load-mode",
    ):
        with pytest.raises(ValueError, match="first-class flag"):
            _driver(extraArgs=[flag, "value"])
    with pytest.raises(ValueError, match="first-class flag"):
        _driver(extraArgs=["--load-mode=mlock"])


def test_reasoning_effort_maps_to_request_payload_without_model_reload():
    """Reasoning effort remains request-scoped and maps to reasoning_effort."""
    request = LlmCallRequest(
        query=LlmQuery(formatId="text/plain", payload="question"),
        model="model-a",
        providerOptions={"reasoningEffort": "high"},
    )
    options = llamaCpp._parseInferenceOptions(request.providerOptions)
    payload = llamaCpp._buildPayload(request, options, includeRequestedModel=False)
    assert payload["reasoning_effort"] == "high"
    assert "model" not in payload
    with pytest.raises(ValueError, match="non-empty text"):
        llamaCpp._parseInferenceOptions({"reasoningEffort": "  "})


def test_execution_profile_exposes_launch_profile_and_exact_token_estimator(monkeypatch):
    """Execution evidence identifies launch settings and retains exact token estimation."""
    driver = _driver(
        contextWindowTokens=16384,
        gpuLayers=999,
        cpuMoeLayers=31,
        loadMode="mlock",
    )
    calls = []

    def postJson(endpoint, payload, *, timeoutSeconds):
        """Returns deterministic template/tokenization responses for the estimator."""
        calls.append((endpoint, payload, timeoutSeconds))
        if endpoint == "/apply-template":
            return {"prompt": "<chat>hello</chat>"}
        if endpoint == "/tokenize":
            return {"tokens": [1, 2, 3]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(driver, "postJson", postJson)
    provider = llamaCpp.LlamaCppStreamProvider(driver=driver)
    profile = provider.getExecutionProfile(
        model="model-a",
        providerOptions={"timeoutSeconds": 7.5},
    )
    launch = profile.metadata["launchProfile"]
    assert profile.contextWindowTokens == 16384
    assert launch["parallelSlots"] == 1
    assert launch["gpuLayers"] == 999
    assert launch["cpuMoeLayers"] == 31
    assert launch["loadMode"] == "mlock"
    assert profile.tokenEstimator is not None
    assert profile.tokenEstimator.estimateInputTokens(
        LlmQuery(formatId="text/plain", payload="hello")
    ) == 3
    assert calls == [
        (
            "/apply-template",
            {"messages": [{"role": "user", "content": "hello"}]},
            7.5,
        ),
        (
            "/tokenize",
            {
                "content": "<chat>hello</chat>",
                "add_special": False,
                "parse_special": True,
                "with_pieces": False,
            },
            7.5,
        ),
    ]
