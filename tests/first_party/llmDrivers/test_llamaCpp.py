from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.llm.llmTypes import LlmQuery


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


def _driver(**modelOverrides):
    model = {"modelPath": "model.gguf", **modelOverrides}
    return llamaCpp.LlamaCppDriver(
        {
            "manageServer": False,
            "baseUrl": "  http://127.0.0.1:8080/  ",
            "models": {"model-a": model},
        }
    )


def test_model_defaults_to_one_parallel_slot_and_server_args_emit_it():
    driver = _driver(contextWindowTokens=8192, gpuLayers=12, threads=4)
    model = driver.models["model-a"]
    assert model.parallelSlots == 1
    assert driver.baseUrl == "http://127.0.0.1:8080"

    driver.executable = Path("llama-server.exe")
    args = driver._serverArgs(model)
    parallelIndex = args.index("--parallel")
    assert args[parallelIndex + 1] == "1"
    assert args[args.index("-c") + 1] == "8192"


def test_parallel_slots_can_be_configured_but_not_duplicated_in_extra_args():
    driver = _driver(parallelSlots=3)
    assert driver.models["model-a"].parallelSlots == 3

    with pytest.raises(ValueError, match="parallelSlots"):
        _driver(extraArgs=["--parallel", "2"])

    with pytest.raises(ValueError, match="positive exact integer"):
        _driver(parallelSlots=0)


def test_execution_profile_exposes_exact_token_estimator(monkeypatch):
    driver = _driver(contextWindowTokens=16384)
    calls = []

    def postJson(endpoint, payload, *, timeoutSeconds):
        calls.append((endpoint, payload, timeoutSeconds))
        if endpoint == "/apply-template":
            return {"prompt": "<chat>hello</chat>"}
        if endpoint == "/tokenize":
            return {"tokens": [1, 2, 3]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(driver, "postJson", postJson)
    provider = llamaCpp.LlamaCppStreamProvider(driver=driver)
    profile = provider.getExecutionProfile(model="model-a", providerOptions={"timeoutSeconds": 7.5})

    assert profile.contextWindowTokens == 16384
    assert profile.metadata["parallelSlots"] == 1
    assert profile.tokenEstimator is not None
    assert profile.tokenEstimator.estimateInputTokens(LlmQuery(formatId="text/plain", payload="hello")) == 3
    assert calls == [
        ("/apply-template", {"messages": [{"role": "user", "content": "hello"}]}, 7.5),
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
