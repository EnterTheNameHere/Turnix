from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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
_SPEC = importlib.util.spec_from_file_location("evilBirthdayAnalysisCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
analysis = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(analysis)


class _MaterializationCapabilities:
    def __init__(self):
        self.rawChatCalls: list[dict[str, object]] = []
        self.interpretCalls: list[dict[str, object]] = []

    def call(self, capabilityId, payload):
        if capabilityId == "evilAnalysis.chat@1":
            self.rawChatCalls.append(payload)
            startVideo = payload["videoStartSeconds"]
            endVideo = payload["videoEndSeconds"]
            streamStart = startVideo - 533
            streamEnd = endVideo - 533
            return {
                "sourcePath": "data/chat.txt",
                "chatStartTime": "19:08:55",
                "streamStartTime": "00:08:53",
                "streamStartVideoSeconds": 533.0,
                "wallClockAtMediaZero": "2024-03-25 19:08:55",
                "wallClockAtStreamZero": "2024-03-25 19:17:48",
                "videoStartSeconds": float(startVideo),
                "videoEndSeconds": float(endVideo),
                "streamStartSeconds": float(streamStart),
                "streamEndSeconds": float(streamEnd),
                "lookbackSeconds": float(payload["lookbackSeconds"]),
                "records": [
                    {
                        "lineNumber": 1,
                        "channel": "#vedal987",
                        "message": "prior: context",
                        "timestampText": "2024-03-25 19:17:47",
                        "streamTimeSeconds": float(streamStart) - 1.0,
                        "streamTime": "-00:00:01",
                        "insideRequestedWindow": False,
                    },
                    {
                        "lineNumber": 2,
                        "channel": "#vedal987",
                        "message": "viewer: hello",
                        "timestampText": "2024-03-25 19:17:48",
                        "streamTimeSeconds": float(streamStart),
                        "streamTime": "00:00:00",
                        "insideRequestedWindow": True,
                    },
                    {
                        "lineNumber": 3,
                        "channel": "#vedal987",
                        "message": "fossabot: hidden",
                        "timestampText": "2024-03-25 19:17:49",
                        "streamTimeSeconds": float(streamStart) + 1.0,
                        "streamTime": "00:00:01",
                        "insideRequestedWindow": True,
                    },
                ],
            }

        if capabilityId == "evilAnalysis.chatInterpret@1":
            self.interpretCalls.append(payload)
            rawRecords = payload["records"]
            records = []
            for rawRecord in rawRecords:
                record = dict(rawRecord)
                if record["lineNumber"] == 1:
                    record.update(
                        username="prior",
                        body="context",
                        analysis={
                            "kind": "userMessage",
                            "includedInText": False,
                            "streamTimeSeconds": record["streamTimeSeconds"],
                            "streamTime": record["streamTime"],
                            "spans": [{"kind": "text", "text": "context"}],
                        },
                    )
                elif record["lineNumber"] == 2:
                    record.update(
                        username="viewer",
                        body="hello",
                        analysis={
                            "kind": "userMessage",
                            "includedInText": True,
                            "streamTimeSeconds": record["streamTimeSeconds"],
                            "streamTime": record["streamTime"],
                            "spans": [{"kind": "text", "text": "hello"}],
                        },
                    )
                else:
                    record.update(
                        username="fossabot",
                        body="hidden",
                        analysis={
                            "kind": "botEvent",
                            "includedInText": False,
                            "streamTimeSeconds": record["streamTimeSeconds"],
                            "streamTime": record["streamTime"],
                        },
                    )
                records.append(record)
            included = [record for record in records if record["analysis"]["includedInText"]]
            return {
                "records": records,
                "text": "\n".join(
                    f"{record['streamTime']} {record['username']}: {record['body']}"
                    for record in included
                ),
            }

        raise AssertionError(capabilityId)


class _MaterializationCtx:
    def __init__(self):
        self.capabilities = _MaterializationCapabilities()


class _BuildQueryCapabilities:
    def __init__(self, tokenCounter=None):
        self.identityPayload = None
        self.tokenTexts: list[str] = []
        self._tokenCounter = tokenCounter or (lambda text: len(text.split()))

    def call(self, capabilityId, payload):
        if capabilityId == "evilAnalysis.identity@1":
            self.identityPayload = payload
            texts = [
                text.replace("viewer_name", "anonymized_1").replace("vedal987", "Vedal")
                for text in payload["texts"]
            ]
            return {
                "displayAuthors": [
                    "anonymized_1" if author == "viewer_name" else "Vedal"
                    for author in payload["authors"]
                ],
                "texts": texts,
                "anonymousIdentityCount": 1,
                "preservedIdentityCount": 1,
            }
        if capabilityId == "evilAnalysis.tokenBudget@1":
            text = payload["text"]
            self.tokenTexts.append(text)
            return {"inputTokens": self._tokenCounter(text)}
        raise AssertionError(capabilityId)


class _BuildQueryCtx:
    def __init__(self, tokenCounter=None, *, optionalFraction=0.60):
        self.capabilities = _BuildQueryCapabilities(tokenCounter)
        self.config = {"chatBudget": {"optionalContextMaxFraction": optionalFraction}}


def _queryItems() -> list[QueryItem]:
    return [
        QueryItem(itemId="context", kind="context", content="context"),
        QueryItem(itemId="profile", kind="analysis-profile", content="profile"),
        QueryItem(itemId="prompt", kind="prompt", content="prompt"),
        QueryItem(
            itemId="t1",
            kind="transcript",
            content="first",
            metadata={
                "streamStartSeconds": 45.0,
                "streamTime": "00:00:45",
                "segmentIndex": 1,
            },
        ),
        QueryItem(
            itemId="t2",
            kind="transcript",
            content="viewer_name mentioned vedal987",
            metadata={
                "streamStartSeconds": 48.0,
                "streamTime": "00:00:48",
                "segmentIndex": 2,
            },
        ),
        QueryItem(
            itemId="chat:20",
            kind="chat",
            content="GIGAEVIL",
            metadata={
                "streamStartSeconds": 45.0,
                "lineNumber": 20,
                "username": "viewer_name",
                "sourceUsername": "viewer_name",
                "analysis": {"streamTime": "00:00:45"},
            },
        ),
        QueryItem(
            itemId="chat:21",
            kind="chat",
            content="replying to viewer_name",
            metadata={
                "streamStartSeconds": 46.0,
                "lineNumber": 21,
                "username": "vedal987",
                "sourceUsername": "vedal987",
                "analysis": {"streamTime": "00:00:46"},
            },
        ),
    ]


def _window() -> dict[str, object]:
    return {
        "positionSeconds": 0,
        "chunkSeconds": 600,
        "chunks": analysis._windowChunks(
            positionSeconds=0,
            chunkSeconds=600,
            offsetsSeconds=(0, -600, -1800),
            streamStartVideoSeconds=533,
        ),
    }


def _queryPayload(*, includeChat: bool, chatLayout: str) -> dict[str, object]:
    return {
        "input": {
            "profile": {
                "name": "default",
                "settings": {"includeChat": includeChat, "chatLayout": chatLayout},
            },
            "promptName": "main",
            "windowIndex": 0,
            "window": _window(),
        },
        "queryItems": [item.snapshot() for item in _queryItems()],
        "execution": {
            "contextWindowTokens": 1000,
            "providerOptions": {"maxTokens": 100},
        },
    }


def test_windowChunks_translate_stream_relative_ranges_to_video_time():
    chunks = analysis._windowChunks(
        positionSeconds=0,
        chunkSeconds=600,
        offsetsSeconds=(0, -600, -1800),
        streamStartVideoSeconds=533,
    )

    assert chunks[0] == {
        "offsetSeconds": 0,
        "streamStartSeconds": 0,
        "streamEndSeconds": 600,
        "videoStartSeconds": 533,
        "videoEndSeconds": 1133,
    }
    assert chunks[1]["videoStartSeconds"] == -67
    assert chunks[2]["videoStartSeconds"] == -1267


def test_interpretedChat_uses_raw_capability_with_semantic_lookback_then_semantics_capability():
    ctx = _MaterializationCtx()

    rawChat, interpreted = analysis._interpretedChat(
        ctx,
        {"videoStartSeconds": 533, "videoEndSeconds": 1133},
    )

    assert ctx.capabilities.rawChatCalls == [
        {
            "videoStartSeconds": 533,
            "videoEndSeconds": 1133,
            "lookbackSeconds": analysis._CHAT_SEMANTIC_LOOKBACK_SECONDS,
        }
    ]
    assert len(ctx.capabilities.interpretCalls) == 1
    assert ctx.capabilities.interpretCalls[0]["records"] is rawChat["records"]
    assert interpreted["sourcePath"] == "data/chat.txt"
    assert interpreted["text"] == "00:00:00 viewer: hello"


def test_preparedChatSnapshot_omits_raw_records_and_counts_only_requested_window_as_source():
    ctx = _MaterializationCtx()
    chunks = analysis._windowChunks(
        positionSeconds=0,
        chunkSeconds=600,
        offsetsSeconds=(0, -600, -1800),
        streamStartVideoSeconds=533,
    )

    snapshot = analysis._preparedChatSnapshot(ctx, {"chunks": chunks}, includedInPrompt=True)

    assert snapshot["prepared"] is True
    assert snapshot["includedInPrompt"] is True
    assert all("records" not in chunk for chunk in snapshot["chunks"])
    assert snapshot["statistics"]["sourceRecordCount"] == 6
    assert snapshot["statistics"]["includedRecordCount"] == 3
    assert snapshot["statistics"]["suppressedRecordCount"] == 3
    assert snapshot["statistics"]["renderedLineCount"] == 3
    assert len(ctx.capabilities.rawChatCalls) == 3
    assert len(ctx.capabilities.interpretCalls) == 3
    assert all(
        call["lookbackSeconds"] == analysis._CHAT_SEMANTIC_LOOKBACK_SECONDS
        for call in ctx.capabilities.rawChatCalls
    )
    assert all(
        chunk["metadata"]["lookbackSeconds"] == analysis._CHAT_SEMANTIC_LOOKBACK_SECONDS
        for chunk in snapshot["chunks"]
    )


def test_transcriptQueryItems_keep_spoken_text_separate_from_stream_time():
    transcript = {
        "sourcePath": "data/transcript.json",
        "streamStartVideoSeconds": 533.0,
        "segments": [
            {
                "segmentIndex": 7,
                "streamStartTime": "00:00:45",
                "words": [
                    {"word": "first", "start": 45.25, "end": 45.50},
                    {"word": "line", "start": 45.55, "end": 46.00},
                ],
            }
        ],
    }

    items = analysis._transcriptQueryItems(transcript, previous={})

    assert len(items) == 1
    assert items[0].content == "first line"
    assert items[0].metadata["streamStartSeconds"] == 45.25
    assert items[0].metadata["streamEndSeconds"] == 46.0
    assert items[0].metadata["streamTime"] == "00:00:45"
    assert items[0].metadata["segmentIndex"] == 7


def test_chatQueryItems_use_interpreted_body_but_keep_raw_message_as_source_evidence():
    interpreted = {
        "sourcePath": "data/chat.txt",
        "records": [
            {
                "lineNumber": 20,
                "channel": "#vedal987",
                "message": "viewer_name: GIGAEVIL",
                "username": "viewer_name",
                "body": "GIGAEVIL",
                "timestampText": "2024-03-25 19:18:33",
                "analysis": {
                    "kind": "userMessage",
                    "includedInText": True,
                    "streamTimeSeconds": 45.0,
                    "streamTime": "00:00:45",
                    "spans": [],
                },
            },
            {
                "lineNumber": 21,
                "channel": "#vedal987",
                "message": "a future source form we do not understand",
                "timestampText": "2024-03-25 19:18:34",
                "analysis": {
                    "kind": "unknownMessage",
                    "includedInText": True,
                    "streamTimeSeconds": 46.0,
                    "streamTime": "00:00:46",
                    "rawMessage": "a future source form we do not understand",
                },
            },
        ],
    }

    items = analysis._chatQueryItems(interpreted, previous={})

    assert len(items) == 2
    assert items[0].content == "GIGAEVIL"
    assert items[0].metadata["username"] == "viewer_name"
    assert items[0].metadata["sourceUsername"] == "viewer_name"
    assert items[0].metadata["source"]["rawMessage"] == "viewer_name: GIGAEVIL"
    assert items[1].content == "a future source form we do not understand"
    assert items[1].metadata["username"] == "[unclassified]"
    assert items[1].metadata["sourceUsername"] is None
    assert items[1].metadata["source"]["rawMessage"] == "a future source form we do not understand"

def test_buildQuery_preserves_unknown_chat_as_unclassified_evidence():
    ctx = _BuildQueryCtx()
    items = _queryItems()
    items.append(
        QueryItem(
            itemId="chat:22",
            kind="chat",
            content="a future source form we do not understand",
            metadata={
                "streamStartSeconds": 47.0,
                "lineNumber": 22,
                "username": "[unclassified]",
                "sourceUsername": None,
                "analysis": {"streamTime": "00:00:47"},
            },
        )
    )
    payload = _queryPayload(includeChat=True, chatLayout="interleaved")
    payload["queryItems"] = [item.snapshot() for item in items]

    query = analysis._buildQuery(ctx, payload)

    assert "[00:00:47 CHAT [unclassified]] a future source form we do not understand" in query["payload"]
    assert ctx.capabilities.identityPayload["authors"] == ["viewer_name", "vedal987"]



def test_buildQuery_can_exclude_chat_while_still_using_chat_authors_for_identity_sanitization():
    ctx = _BuildQueryCtx()
    query = analysis._buildQuery(ctx, _queryPayload(includeChat=False, chatLayout="separate"))

    assert ctx.capabilities.identityPayload["authors"] == ["viewer_name", "vedal987"]
    assert "GIGAEVIL" not in query["payload"]
    assert "viewer_name" not in query["payload"]
    assert "vedal987" not in query["payload"]
    assert "[00:00:45 EVIL] first" in query["payload"]
    assert "[00:00:48 EVIL] anonymized_1 mentioned Vedal" in query["payload"]
    assert query["metadata"]["chatIncluded"] is False


def test_buildQuery_separate_layout_renders_sanitized_chat():
    ctx = _BuildQueryCtx()
    query = analysis._buildQuery(ctx, _queryPayload(includeChat=True, chatLayout="separate"))

    expectedTranscript = (
        "TRANSCRIPT WINDOW\n"
        "[00:00:45 EVIL] first\n"
        "[00:00:48 EVIL] anonymized_1 mentioned Vedal"
    )
    expectedChat = (
        "CHAT WINDOW\n"
        "[00:00:45 CHAT anonymized_1] GIGAEVIL\n"
        "[00:00:46 CHAT Vedal] replying to anonymized_1"
    )
    assert expectedTranscript in query["payload"]
    assert expectedChat in query["payload"]
    assert "viewer_name" not in query["payload"]
    assert "vedal987" not in query["payload"]
    assert query["metadata"]["identitySanitized"] is True


def test_buildQuery_interleaved_layout_orders_chat_and_transcript_by_stream_time():
    ctx = _BuildQueryCtx()
    query = analysis._buildQuery(ctx, _queryPayload(includeChat=True, chatLayout="interleaved"))

    expectedEvidence = (
        "CHRONOLOGICAL EVIDENCE\n"
        "[00:00:45 EVIL] first\n"
        "[00:00:45 CHAT anonymized_1] GIGAEVIL\n"
        "[00:00:46 CHAT Vedal] replying to anonymized_1\n"
        "[00:00:48 EVIL] anonymized_1 mentioned Vedal"
    )
    assert expectedEvidence in query["payload"]
    assert "TRANSCRIPT 00:00:45" not in query["payload"]
    assert "CHAT 00:00:45" not in query["payload"]


def test_chatPresentation_validation_is_unchanged():
    assert analysis._chatPresentation({}) == (False, "separate")
    assert analysis._chatPresentation({"includeChat": True, "chatLayout": "interleaved"}) == (True, "interleaved")
    with pytest.raises(TypeError, match="includeChat"):
        analysis._chatPresentation({"includeChat": 1})
    with pytest.raises(ValueError, match="chatLayout"):
        analysis._chatPresentation({"chatLayout": "mixed")
