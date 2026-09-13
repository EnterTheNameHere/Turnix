# file: tests/first_party/llmDrivers/test_llamaCppStructuredMessages.py ; version: 1
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.llm.llmTypes import LlmCallRequest, LlmQuery
from backend.llm.structuredMessages import (
    LLM_MESSAGES_FORMAT_ID,
    LlmMessage,
    LlmMessages,
)


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "llmDrivers"
    / "llamaCpp"
    / "structuredCodeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "llamaCppStructuredCodeEntryTest",
    _CODE_ENTRY,
)
assert _SPEC is not None and _SPEC.loader is not None
llamaCpp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(llamaCpp)


def _driver():
    """Creates one external-server driver that requires no filesystem model."""
    return llamaCpp.LlamaCppDriver(
        {
            "manageServer": False,
            "baseUrl": "http://127.0.0.1:8080",
            "models": {"model-a": {"modelPath": "model.gguf"}},
        }
    )


def _conversation() -> LlmMessages:
    """Returns a conversation containing text whose exact boundaries matter."""
    return LlmMessages(
        (
            LlmMessage(role="user", content="grounding\r\nexact"),
            LlmMessage(role="assistant", content="response\nwith newline"),
            LlmMessage(role="user", content="fix: keep  spaces"),
        )
    )


def test_structured_messages_round_trip_exactly() -> None:
    """Canonical durable payload round-trips roles, order, and exact text."""
    messages = _conversation()
    query = messages.toQuery()

    assert query.formatId == LLM_MESSAGES_FORMAT_ID
    assert type(query.payload) is bytes
    assert LlmMessages.fromPayload(query.payload) == messages


def test_structured_payload_maps_directly_to_provider_messages() -> None:
    """Provider payload preserves ordered turns without synthetic messages."""
    messages = _conversation()
    request = LlmCallRequest(query=messages.toQuery(), model="model-a")
    options = llamaCpp.LlamaCppOptions(temperature=0.25)

    payload = llamaCpp._buildPayload(
        request,
        options,
        includeRequestedModel=True,
    )

    assert payload["messages"] == messages.snapshot()
    assert payload["model"] == "model-a"
    assert payload["temperature"] == 0.25


def test_text_plain_payload_behavior_remains_single_user_message() -> None:
    """Legacy text/plain queries retain their existing one-user-message mapping."""
    request = LlmCallRequest(
        query=LlmQuery(formatId="text/plain", payload="hello"),
    )

    payload = llamaCpp._buildPayload(
        request,
        llamaCpp.LlamaCppOptions(),
        includeRequestedModel=False,
    )

    assert payload["messages"] == [{"role": "user", "content": "hello"}]


def test_token_estimator_templates_the_same_structured_messages(monkeypatch) -> None:
    """Token estimation sends exactly the inference message array to apply-template."""
    driver = _driver()
    calls: list[tuple[str, dict[str, object], float]] = []

    def postJson(
        endpoint: str,
        payload: dict[str, object],
        *,
        timeoutSeconds: float,
    ) -> dict[str, object]:
        """Captures estimator HTTP payloads and returns deterministic responses."""
        calls.append((endpoint, payload, timeoutSeconds))
        if endpoint == "/apply-template":
            return {"prompt": "templated"}
        if endpoint == "/tokenize":
            return {"tokens": [1, 2, 3, 4]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(driver, "postJson", postJson)
    estimator = llamaCpp.LlamaCppTokenEstimator(
        driver=driver,
        timeoutSeconds=9.0,
    )
    messages = _conversation()

    assert estimator.estimateInputTokens(messages.toQuery()) == 4
    assert calls[0] == (
        "/apply-template",
        {"messages": messages.snapshot()},
        9.0,
    )


def test_invalid_structured_roles_and_payloads_fail_at_contract_boundary() -> None:
    """Invalid roles and noncanonical structured payloads are rejected explicitly."""
    with pytest.raises(ValueError, match="Unsupported LLM message role"):
        LlmMessage(role="system", content="not supported")

    query = LlmQuery(
        formatId=LLM_MESSAGES_FORMAT_ID,
        payload=b'[{"role":"user","content":"x"}] ',
    )
    request = LlmCallRequest(query=query)
    with pytest.raises(ValueError, match="canonical"):
        llamaCpp._buildPayload(
            request,
            llamaCpp.LlamaCppOptions(),
            includeRequestedModel=False,
        )
