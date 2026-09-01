from __future__ import annotations

import importlib.util
from pathlib import Path


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
