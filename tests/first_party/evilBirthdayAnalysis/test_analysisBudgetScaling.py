from __future__ import annotations

import importlib.util
from pathlib import Path

from backend.processing.runtime import QueryItem


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "evilBirthdayAnalysis"
    / "packs"
    / "analysis"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayAnalysisScalingCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
analysis = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(analysis)


class _Capabilities:
    def __init__(self):
        self.tokenMeasurements = 0

    def call(self, capabilityId, payload):
        if capabilityId == "evilAnalysis.identity@1":
            authors = payload["authors"]
            return {
                "displayAuthors": ["anonymized_1" for _ in authors],
                "texts": [text.replace("viewer", "anonymized_1") for text in payload["texts"]],
                "anonymousIdentityCount": 1 if authors else 0,
                "preservedIdentityCount": 0,
            }
        if capabilityId == "evilAnalysis.tokenBudget@1":
            self.tokenMeasurements += 1
            text = payload["text"]
            # Deterministic monotonic stand-in for an exact tokenizer. Fixed
            # material costs 20, current chat costs 5, every optional line 2.
            return {
                "inputTokens": (
                    20
                    + text.count("CURRENT") * 5
                    + text.count("OPTIONAL") * 2
                )
            }
        raise AssertionError(capabilityId)


class _Ctx:
    def __init__(self):
        self.capabilities = _Capabilities()
        self.config = {"chatBudget": {"optionalContextMaxFraction": 0.60}}


def _chat(itemId: str, *, streamTime: float, lineNumber: int, content: str) -> QueryItem:
    sign = "-" if streamTime < 0 else ""
    seconds = abs(int(streamTime))
    return QueryItem(
        itemId=itemId,
        kind="chat",
        content=content,
        metadata={
            "streamStartSeconds": streamTime,
            "lineNumber": lineNumber,
            "username": "viewer",
            "sourceUsername": "viewer",
            "analysis": {"streamTime": f"{sign}00:00:{seconds % 60:02d}"},
        },
    )


def test_optional_budget_uses_logarithmic_exact_measurements():
    ctx = _Ctx()
    current = _chat("chat:current", streamTime=10.0, lineNumber=1000, content="CURRENT")
    optional = [
        _chat(
            f"chat:old:{index}",
            streamTime=-float(index + 1),
            lineNumber=999 - index,
            content="OPTIONAL",
        )
        for index in range(256)
    ]
    byKind = {
        "context": [QueryItem(itemId="context", kind="context", content="context")],
        "analysis-profile": [QueryItem(itemId="profile", kind="analysis-profile", content="profile")],
        "prompt": [QueryItem(itemId="prompt", kind="prompt", content="prompt")],
        "transcript": [
            QueryItem(
                itemId="transcript",
                kind="transcript",
                content="transcript",
                metadata={
                    "streamStartSeconds": 1.0,
                    "streamTime": "00:00:01",
                    "segmentIndex": 1,
                },
            )
        ],
        "chat": [current, *optional],
    }
    inputValue = {
        "window": {
            "positionSeconds": 0,
            "chunkSeconds": 600,
            "chunks": [
                {
                    "offsetSeconds": 0,
                    "streamStartSeconds": 0,
                    "streamEndSeconds": 600,
                    "videoStartSeconds": 533,
                    "videoEndSeconds": 1133,
                },
                {
                    "offsetSeconds": -600,
                    "streamStartSeconds": -600,
                    "streamEndSeconds": 0,
                    "videoStartSeconds": -67,
                    "videoEndSeconds": 533,
                },
                {
                    "offsetSeconds": -1800,
                    "streamStartSeconds": -1800,
                    "streamEndSeconds": -1200,
                    "videoStartSeconds": -1267,
                    "videoEndSeconds": -667,
                },
            ],
        }
    }

    presented, evidence, _identity = analysis._budgetedChat(
        ctx,
        inputValue=inputValue,
        execution={"contextWindowTokens": 100, "providerOptions": {"maxTokens": 10}},
        byKind=byKind,
        allChatItems=byKind["chat"],
        chatLayout="separate",
    )

    assert presented[0] is current
    assert evidence["optionalTruncated"] is True
    assert evidence["optionalIncludedItemCount"] < 256
    assert evidence["promptNotice"] == analysis._CHAT_BUDGET_PROMPT_NOTICE
    assert evidence["warnings"] == [
        (
            "Chat budget truncation: "
            f"included {evidence['optionalIncludedItemCount']} of 256 optional older chat messages; "
            f"omitted {256 - evidence['optionalIncludedItemCount']} lower-priority messages. "
            "Current-window chat remains complete."
        )
    ]
    assert evidence["tokenMeasurementCount"] == ctx.capabilities.tokenMeasurements
    # Truncation causes a second logarithmic pass because the model-facing
    # notice itself is measured inside the final token budget.
    assert ctx.capabilities.tokenMeasurements <= 28
