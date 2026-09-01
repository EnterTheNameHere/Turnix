from __future__ import annotations

import pytest

from backend.llm.llmTypes import LlmCallRequest, LlmExecutionProfile, LlmQuery, LlmStreamEvent


def test_query_freezes_nested_metadata_without_changing_payload_identity() -> None:
    payload = {"messages": ["hello"]}
    metadata = {"source": {"indices": [1, 2]}}
    query = LlmQuery(formatId="application/test", payload=payload, metadata=metadata)

    metadata["source"]["indices"].append(3)
    payload["messages"].append("world")

    assert query.payload is payload
    assert query.metadata["source"]["indices"] == (1, 2)
    assert query.payload["messages"] == ["hello", "world"]


def test_call_request_freezes_provider_options() -> None:
    options = {"sampling": {"temperature": 0.7}}
    request = LlmCallRequest(
        query=LlmQuery(formatId="text/plain", payload="hello"),
        model="model-a",
        providerOptions=options,
    )

    options["sampling"]["temperature"] = 1.2
    assert request.providerOptions["sampling"]["temperature"] == 0.7


def test_completed_stream_event_cannot_carry_text() -> None:
    with pytest.raises(ValueError):
        LlmStreamEvent(eventType="completed", text="not allowed")


def test_execution_profile_rejects_non_positive_context_window() -> None:
    with pytest.raises(ValueError):
        LlmExecutionProfile(contextWindowTokens=0)
