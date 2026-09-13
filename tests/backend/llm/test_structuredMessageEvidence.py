# file: tests/backend/llm/test_structuredMessageEvidence.py ; version: 1
from __future__ import annotations

import base64

from backend.llm.streamingRuntime import LlmProcessingPipeline
from backend.llm.structuredMessages import LlmMessage, LlmMessages


def test_processing_query_evidence_retains_exact_structured_payload() -> None:
    """ProcessingRun query evidence can reconstruct the exact structured bytes."""
    query = LlmMessages(
        (
            LlmMessage(role="user", content="one\r\n"),
            LlmMessage(role="assistant", content="two\n"),
            LlmMessage(role="user", content="three  spaces"),
        )
    ).toQuery()

    evidence = LlmProcessingPipeline._queryEvidence(query)

    assert evidence["formatId"] == "actant.llm.messages@1"
    assert evidence["payloadType"] == "bytes"
    encoded = evidence["payloadBase64"]
    assert isinstance(encoded, str)
    assert base64.b64decode(encoded) == query.payload
