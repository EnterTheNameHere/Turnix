from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


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


class _Ctx:
    config = {
        "llm": {"providerOptions": {"timeoutSeconds": 30}},
        "llamaCpp": {"baseUrl": "http://127.0.0.1:8080"},
    }


def test_estimateText_applies_chat_template_before_tokenization(monkeypatch):
    calls = []

    def fakePost(_ctx, endpoint, payload):
        calls.append((endpoint, payload))
        if endpoint == "/apply-template":
            return {"prompt": "<chat>hello</chat>"}
        if endpoint == "/tokenize":
            return {"tokens": [1, 2, 3, 4]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(tokenBudget, "_postJson", fakePost)

    assert tokenBudget._estimateText(_Ctx(), "hello") == 4
    assert calls == [
        ("/apply-template", {"messages": [{"role": "user", "content": "hello"}]}),
        (
            "/tokenize",
            {
                "content": "<chat>hello</chat>",
                "add_special": False,
                "parse_special": True,
                "with_pieces": False,
            },
        ),
    ]


def test_measure_returns_only_input_token_count(monkeypatch):
    monkeypatch.setattr(tokenBudget, "_estimateText", lambda _ctx, text: len(text))

    assert tokenBudget._measure(_Ctx(), {"text": "abcd"}) == {"inputTokens": 4}


def test_timeout_rejects_non_finite_values():
    class Ctx:
        config = {
            "llm": {"providerOptions": {"timeoutSeconds": float("nan")}},
            "llamaCpp": {"baseUrl": "http://127.0.0.1:8080"},
        }

    with pytest.raises(ValueError, match="positive finite"):
        tokenBudget._timeoutSeconds(Ctx())


def test_baseUrl_strips_whitespace_and_rejects_non_http_scheme():
    class SpacedCtx:
        config = {"llamaCpp": {"baseUrl": "  http://127.0.0.1:8080/  "}}

    assert tokenBudget._baseUrl(SpacedCtx()) == "http://127.0.0.1:8080"

    class BadCtx:
        config = {"llamaCpp": {"baseUrl": "file:///tmp/llama"}}

    with pytest.raises(ValueError, match="http:// or https://"):
        tokenBudget._baseUrl(BadCtx())


def test_postJson_rejects_relative_endpoint_before_network_access():
    with pytest.raises(ValueError, match="absolute HTTP path"):
        tokenBudget._postJson(_Ctx(), "tokenize", {})
