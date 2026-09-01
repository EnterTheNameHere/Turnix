from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.llm.llmTypes import LlmQuery


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "evilBirthdayAnalysis"
    / "packs"
    / "tokenBudget"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayTokenBudgetCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
tokenBudget = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tokenBudget)


class _Llm:
    def __init__(self):
        self.calls = []

    def estimateInputTokens(self, **kwargs):
        self.calls.append(kwargs)
        return len(kwargs["query"].payload)


class _Ctx:
    def __init__(self):
        self.config = {
            "llm": {
                "provider": "llama.cpp",
                "model": "model-b",
                "providerOptions": {"timeoutSeconds": 30},
            }
        }
        self.llm = _Llm()


def test_estimateText_uses_provider_neutral_llm_facade():
    ctx = _Ctx()

    assert tokenBudget._estimateText(ctx, "hello") == 5
    assert len(ctx.llm.calls) == 1
    call = ctx.llm.calls[0]
    assert call["providerName"] == "llama.cpp"
    assert call["model"] == "model-b"
    assert call["providerOptions"] == {"timeoutSeconds": 30}
    assert isinstance(call["query"], LlmQuery)
    assert call["query"].formatId == "text/plain"
    assert call["query"].payload == "hello"


def test_measure_returns_only_input_token_count(monkeypatch):
    monkeypatch.setattr(tokenBudget, "_estimateText", lambda _ctx, text: len(text))

    assert tokenBudget._measure(_Ctx(), {"text": "abcd"}) == {"inputTokens": 4}


def test_llm_selection_requires_provider_and_valid_options():
    ctx = _Ctx()
    del ctx.config["llm"]["provider"]
    with pytest.raises(ValueError, match="llm.provider"):
        tokenBudget._llmSelection(ctx)

    ctx = _Ctx()
    ctx.config["llm"]["providerOptions"] = []
    with pytest.raises(ValueError, match="providerOptions"):
        tokenBudget._llmSelection(ctx)
