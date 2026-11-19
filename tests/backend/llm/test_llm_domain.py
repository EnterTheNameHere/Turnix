from __future__ import annotations

import pytest

from backend.llm.llmTypes import (
    LlmCallRequest,
    LlmExecutionProfile,
    LlmQuery,
    LlmQueryBudget,
    LlmStreamEvent,
)


class Estimator:
    def estimateInputTokens(self, query):
        return len(str(query.payload))


class NotEstimator:
    pass


def test_query_keeps_opaque_payload_identity_and_freezes_nested_metadata() -> None:
    payload = {"messages": ["hello"]}
    metadata = {"source": {"indices": [1, 2]}}
    query = LlmQuery(formatId="application/test", payload=payload, metadata=metadata)

    metadata["source"]["indices"].append(3)
    payload["messages"].append("world")

    assert query.payload is payload
    assert query.metadata["source"]["indices"] == (1, 2)
    assert query.payload["messages"] == ["hello", "world"]


def test_query_requires_non_blank_exact_string_format_id() -> None:
    with pytest.raises(ValueError):
        LlmQuery(formatId="   ", payload=None)
    with pytest.raises(TypeError):
        LlmQuery(formatId=123, payload=None)


def test_call_request_freezes_provider_options_without_copying_query_payload() -> None:
    payload = object()
    query = LlmQuery(formatId="application/x-opaque", payload=payload)
    options = {"sampling": {"temperature": 0.7}}
    request = LlmCallRequest(
        query=query,
        model="model-a",
        providerOptions=options,
    )

    options["sampling"]["temperature"] = 1.2
    assert request.query is query
    assert request.query.payload is payload
    assert request.providerOptions["sampling"]["temperature"] == 0.7


def test_call_request_validates_query_and_optional_model() -> None:
    with pytest.raises(TypeError):
        LlmCallRequest(query="not-a-query")

    query = LlmQuery(formatId="text/plain", payload="hello")
    with pytest.raises(ValueError):
        LlmCallRequest(query=query, model="")


def test_query_budget_validates_input_and_response_reservation() -> None:
    budget = LlmQueryBudget(maxInputTokens=4096, reservedResponseTokens=512)
    assert budget.maxInputTokens == 4096
    assert budget.reservedResponseTokens == 512

    with pytest.raises(ValueError):
        LlmQueryBudget(maxInputTokens=0)
    with pytest.raises(ValueError):
        LlmQueryBudget(maxInputTokens=1, reservedResponseTokens=-1)


def test_execution_profile_accepts_estimator_and_freezes_metadata() -> None:
    metadata = {"provider": {"features": ["token-counting"]}}
    estimator = Estimator()
    profile = LlmExecutionProfile(
        contextWindowTokens=8192,
        tokenEstimator=estimator,
        metadata=metadata,
    )

    metadata["provider"]["features"].append("changed")
    assert profile.tokenEstimator is estimator
    assert profile.metadata["provider"]["features"] == ("token-counting",)


def test_execution_profile_rejects_invalid_context_window_and_estimator() -> None:
    with pytest.raises(ValueError):
        LlmExecutionProfile(contextWindowTokens=0)
    with pytest.raises(TypeError):
        LlmExecutionProfile(tokenEstimator=NotEstimator())


def test_stream_events_freeze_metadata_and_enforce_event_contract() -> None:
    metadata = {"usage": {"tokens": [1, 2]}}
    delta = LlmStreamEvent(eventType="delta", text="part", metadata=metadata)
    metadata["usage"]["tokens"].append(3)

    assert delta.text == "part"
    assert delta.metadata["usage"]["tokens"] == (1, 2)

    with pytest.raises(ValueError):
        LlmStreamEvent(eventType="completed", text="not allowed")
    with pytest.raises(ValueError):
        LlmStreamEvent(eventType="unknown")
