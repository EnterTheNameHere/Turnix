# file: tests/first_party/evilBirthdayAnalysis/test_analysisChatMaterialization.py ; version: 8
from __future__ import annotations

import importlib.util
import math
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
                "sourceObservation": {
                    "path": "data/chat.txt",
                    "state": "file",
                    "sizeBytes": 100,
                    "modifiedTimeNs": 1,
                    "contentSha256": "chat-source-hash",
                },
                "chatStartTime": "19:08:55",
                "streamStartTime": "00:08:53",
                "streamStartVideoSeconds": 533.0,
                "wallClockAtMediaZero": "2024-03-25 19:08:55",
                "wallClockAtStreamZero": "2024-03-25 19:17:48",
                "videoStartSeconds": float(startVideo),
                "videoEndSeconds": float(endVideo),
                "streamStartSeconds": float(streamStart),
                "streamEndSeconds": float(streamEnd),
                "contextStreamStartSeconds": float(streamStart) - float(payload["lookbackSeconds"]),
                "contextStreamEndSeconds": float(streamEnd) + float(payload["lookaheadSeconds"]),
                "lookbackSeconds": float(payload["lookbackSeconds"]),
                "lookaheadSeconds": float(payload["lookaheadSeconds"]),
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
                semanticAddress = f"evilanalysis/chat/line/{record['lineNumber']}/semantic"
                record["semanticValue"] = {
                    "address": semanticAddress,
                    "dependency": {
                        "address": semanticAddress,
                        "state": "present",
                        "contentSha256": f"semantic-{record['lineNumber']}",
                        "metadataSha256": f"semantic-metadata-{record['lineNumber']}",
                    },
                }
                records.append(record)

            grouped = {}
            for record in records:
                secondIndex = math.floor(float(record["streamTimeSeconds"]))
                grouped.setdefault(secondIndex, []).append(record)

            secondBuckets = []
            for secondIndex in sorted(grouped):
                segment = f"n{-secondIndex}" if secondIndex < 0 else f"s{secondIndex}"
                address = f"evilanalysis/chat/second/{segment}/semantic"
                members = [
                    {
                        "lineNumber": record["lineNumber"],
                        "streamTimeSeconds": float(record["streamTimeSeconds"]),
                        "semantic": record["semanticValue"],
                    }
                    for record in grouped[secondIndex]
                ]
                secondBuckets.append(
                    {
                        "address": address,
                        "dependency": {
                            "address": address,
                            "state": "present",
                            "contentSha256": f"bucket-{secondIndex}",
                            "metadataSha256": f"bucket-metadata-{secondIndex}",
                        },
                        "value": {
                            "secondIndex": secondIndex,
                            "startSeconds": float(secondIndex),
                            "endSeconds": float(secondIndex + 1),
                            "members": members,
                        },
                    }
                )

            secondAggregates = []
            for bucket in secondBuckets:
                entries = [
                    {
                        "kind": "message",
                        "lineNumber": member["lineNumber"],
                        "semantic": member["semantic"],
                    }
                    for member in bucket["value"]["members"]
                ]
                aggregateAddress = bucket["address"].replace("/semantic", "/aggregate")
                secondAggregates.append(
                    {
                        "address": aggregateAddress,
                        "dependency": {
                            "address": aggregateAddress,
                            "state": "present",
                            "contentSha256": f"aggregate-{bucket['value']['secondIndex']}",
                            "metadataSha256": f"aggregate-metadata-{bucket['value']['secondIndex']}",
                        },
                        "value": {
                            "secondIndex": bucket["value"]["secondIndex"],
                            "secondBucket": {
                                "address": bucket["address"],
                                "dependency": bucket["dependency"],
                            },
                            "entries": entries,
                        },
                    }
                )

            included = [record for record in records if record["analysis"]["includedInText"]]
            return {
                "records": records,
                "secondBuckets": secondBuckets,
                "secondAggregates": secondAggregates,
                "identicalMessageBursts": [],
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
    assert ctx.capabilities.interpretCalls[0]["sourcePath"] == "data/chat.txt"
    assert ctx.capabilities.interpretCalls[0]["sourceObservation"] == rawChat["sourceObservation"]
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
        call["lookaheadSeconds"] == analysis._CHAT_SEMANTIC_LOOKAHEAD_SECONDS
        for call in ctx.capabilities.rawChatCalls
    )
    assert all(
        chunk["metadata"]["lookbackSeconds"] == analysis._CHAT_SEMANTIC_LOOKBACK_SECONDS
        for chunk in snapshot["chunks"]
    )
    assert all(
        chunk["metadata"]["lookaheadSeconds"] == analysis._CHAT_SEMANTIC_LOOKAHEAD_SECONDS
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
                "semanticValue": {
                    "address": "evilanalysis/chat/line/20/semantic",
                    "dependency": {
                        "address": "evilanalysis/chat/line/20/semantic",
                        "state": "present",
                        "contentSha256": "semantic-20",
                        "metadataSha256": "metadata-20",
                    },
                },
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
                "semanticValue": {
                    "address": "evilanalysis/chat/line/21/semantic",
                    "dependency": {
                        "address": "evilanalysis/chat/line/21/semantic",
                        "state": "present",
                        "contentSha256": "semantic-21",
                        "metadataSha256": "metadata-21",
                    },
                },
                "analysis": {
                    "kind": "unknownMessage",
                    "includedInText": True,
                    "streamTimeSeconds": 46.0,
                    "streamTime": "00:00:46",
                    "rawMessage": "a future source form we do not understand",
                },
            },
        ],
        "secondBuckets": [
            {
                "address": "evilanalysis/chat/second/s45/semantic",
                "dependency": {
                    "address": "evilanalysis/chat/second/s45/semantic",
                    "state": "present",
                    "contentSha256": "bucket-45",
                    "metadataSha256": "bucket-metadata-45",
                },
                "value": {
                    "secondIndex": 45,
                    "startSeconds": 45.0,
                    "endSeconds": 46.0,
                    "members": [
                        {
                            "lineNumber": 20,
                            "streamTimeSeconds": 45.0,
                            "semantic": {
                                "address": "evilanalysis/chat/line/20/semantic",
                                "dependency": {
                                    "address": "evilanalysis/chat/line/20/semantic",
                                    "state": "present",
                                    "contentSha256": "semantic-20",
                                    "metadataSha256": "metadata-20",
                                },
                            },
                        }
                    ],
                },
            },
            {
                "address": "evilanalysis/chat/second/s46/semantic",
                "dependency": {
                    "address": "evilanalysis/chat/second/s46/semantic",
                    "state": "present",
                    "contentSha256": "bucket-46",
                    "metadataSha256": "bucket-metadata-46",
                },
                "value": {
                    "secondIndex": 46,
                    "startSeconds": 46.0,
                    "endSeconds": 47.0,
                    "members": [
                        {
                            "lineNumber": 21,
                            "streamTimeSeconds": 46.0,
                            "semantic": {
                                "address": "evilanalysis/chat/line/21/semantic",
                                "dependency": {
                                    "address": "evilanalysis/chat/line/21/semantic",
                                    "state": "present",
                                    "contentSha256": "semantic-21",
                                    "metadataSha256": "metadata-21",
                                },
                            },
                        }
                    ],
                },
            },
        ],
        "secondAggregates": [
            {
                "address": "evilanalysis/chat/second/s45/aggregate",
                "dependency": {
                    "address": "evilanalysis/chat/second/s45/aggregate",
                    "state": "present",
                    "contentSha256": "aggregate-45",
                    "metadataSha256": "aggregate-metadata-45",
                },
                "value": {
                    "secondIndex": 45,
                    "secondBucket": {
                        "address": "evilanalysis/chat/second/s45/semantic",
                        "dependency": {
                            "address": "evilanalysis/chat/second/s45/semantic",
                            "state": "present",
                            "contentSha256": "bucket-45",
                            "metadataSha256": "bucket-metadata-45",
                        },
                    },
                    "entries": [
                        {
                            "kind": "message",
                            "lineNumber": 20,
                            "semantic": {
                                "address": "evilanalysis/chat/line/20/semantic",
                                "dependency": {
                                    "address": "evilanalysis/chat/line/20/semantic",
                                    "state": "present",
                                    "contentSha256": "semantic-20",
                                    "metadataSha256": "metadata-20",
                                },
                            },
                        }
                    ],
                },
            },
            {
                "address": "evilanalysis/chat/second/s46/aggregate",
                "dependency": {
                    "address": "evilanalysis/chat/second/s46/aggregate",
                    "state": "present",
                    "contentSha256": "aggregate-46",
                    "metadataSha256": "aggregate-metadata-46",
                },
                "value": {
                    "secondIndex": 46,
                    "secondBucket": {
                        "address": "evilanalysis/chat/second/s46/semantic",
                        "dependency": {
                            "address": "evilanalysis/chat/second/s46/semantic",
                            "state": "present",
                            "contentSha256": "bucket-46",
                            "metadataSha256": "bucket-metadata-46",
                        },
                    },
                    "entries": [
                        {
                            "kind": "message",
                            "lineNumber": 21,
                            "semantic": {
                                "address": "evilanalysis/chat/line/21/semantic",
                                "dependency": {
                                    "address": "evilanalysis/chat/line/21/semantic",
                                    "state": "present",
                                    "contentSha256": "semantic-21",
                                    "metadataSha256": "metadata-21",
                                },
                            },
                        }
                    ],
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
    assert items[0].metadata["memory"]["semantic"]["address"] == "evilanalysis/chat/line/20/semantic"
    assert items[0].metadata["memory"]["secondBucket"]["address"] == "evilanalysis/chat/second/s45/semantic"
    assert items[0].metadata["memory"]["secondAggregate"]["address"] == "evilanalysis/chat/second/s45/aggregate"
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

    assert (
        "[00:00:47]\n"
        "CHAT [unclassified]: a future source form we do not understand"
    ) in query["payload"]
    assert ctx.capabilities.identityPayload["authors"] == ["viewer_name", "vedal987"]


def test_buildQuery_keeps_analysis_profile_out_of_model_facing_prompt():
    ctx = _BuildQueryCtx()
    payload = _queryPayload(includeChat=True, chatLayout="interleaved")
    payload["input"]["profile"]["name"] = "0-10-30-profile"
    payload["input"]["profile"]["description"] = (
        "Use aligned 10-minute context chunks at the current stream position, "
        "10 minutes earlier, and 30 minutes earlier."
    )
    items = _queryItems()
    items[1] = QueryItem(
        itemId="profile",
        kind="analysis-profile",
        content=(
            "Analysis profile: 0-10-30-profile\n"
            "Use aligned 10-minute context chunks at the current stream position, "
            "10 minutes earlier, and 30 minutes earlier."
        ),
        metadata={"profile": payload["input"]["profile"]},
    )
    payload["queryItems"] = [item.snapshot() for item in items]

    query = analysis._buildQuery(ctx, payload)

    assert "ANALYSIS PROFILE" not in query["payload"]
    assert "0-10-30-profile" not in query["payload"]
    assert "10 minutes earlier" not in query["payload"]
    assert query["metadata"]["profileName"] == "0-10-30-profile"


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


def test_buildQuery_interleaved_layout_groups_evidence_into_second_buckets():
    ctx = _BuildQueryCtx()
    query = analysis._buildQuery(ctx, _queryPayload(includeChat=True, chatLayout="interleaved"))

    expectedEvidence = (
        "CHRONOLOGICAL EVIDENCE\n"
        "[00:00:45]\n"
        "EVIL: first\n"
        "CHAT anonymized_1: GIGAEVIL\n\n"
        "[00:00:46]\n"
        "CHAT Vedal: replying to anonymized_1\n\n"
        "[00:00:48]\n"
        "EVIL: anonymized_1 mentioned Vedal"
    )
    assert expectedEvidence in query["payload"]
    assert "EVIDENCE NOTICE" not in query["payload"]
    assert "[00:00:45 EVIL]" not in query["payload"]
    assert "[00:00:45 CHAT" not in query["payload"]


def test_buildQuery_warns_model_when_optional_chat_is_budget_truncated():
    ctx = _BuildQueryCtx(optionalFraction=0.0)
    payload = _queryPayload(includeChat=True, chatLayout="interleaved")
    payload["input"]["window"] = {
        "positionSeconds": 600,
        "chunkSeconds": 600,
        "chunks": analysis._windowChunks(
            positionSeconds=600,
            chunkSeconds=600,
            offsetsSeconds=(0, -600, -1800),
            streamStartVideoSeconds=533,
        ),
    }

    query = analysis._buildQuery(ctx, payload)

    assert (
        "EVIDENCE NOTICE\n"
        + analysis._CHAT_BUDGET_PROMPT_NOTICE
    ) in query["payload"]
    budget = query["metadata"]["chatBudget"]
    assert budget["optionalTruncated"] is True
    assert budget["optionalRequestedItemCount"] == 2
    assert budget["optionalIncludedItemCount"] == 0
    assert budget["warnings"] == [
        (
            "Chat budget truncation: included 0 of 2 optional older chat messages; "
            "omitted 2 lower-priority messages. Current-window chat remains complete."
        )
    ]


def test_buildQuery_interleaved_layout_collapses_same_second_duplicate_user_messages():
    ctx = _BuildQueryCtx()
    items = _queryItems()
    items.extend(
        [
            QueryItem(
                itemId="chat:22",
                kind="chat",
                content="GIGAEVIL",
                metadata={
                    "streamStartSeconds": 45.0,
                    "lineNumber": 22,
                    "username": "other_viewer",
                    "sourceUsername": "other_viewer",
                    "analysis": {"kind": "userMessage", "streamTime": "00:00:45"},
                },
            ),
            QueryItem(
                itemId="chat:23",
                kind="chat",
                content="GIGAEVIL",
                metadata={
                    "streamStartSeconds": 45.0,
                    "lineNumber": 23,
                    "username": "third_viewer",
                    "sourceUsername": "third_viewer",
                    "analysis": {"kind": "userMessage", "streamTime": "00:00:45"},
                },
            ),
        ]
    )
    items[5] = QueryItem(
        itemId="chat:20",
        kind="chat",
        content="GIGAEVIL",
        metadata={
            "streamStartSeconds": 45.0,
            "lineNumber": 20,
            "username": "viewer_name",
            "sourceUsername": "viewer_name",
            "analysis": {"kind": "userMessage", "streamTime": "00:00:45"},
        },
    )
    payload = _queryPayload(includeChat=True, chatLayout="interleaved")
    payload["queryItems"] = [item.snapshot() for item in items]

    query = analysis._buildQuery(ctx, payload)

    assert "CHAT: GIGAEVIL ×3 [3 users]" in query["payload"]
    assert "CHAT anonymized_1: GIGAEVIL" not in query["payload"]


def test_interleaved_chat_presentation_uses_semantic_repeat_spans_without_mutating_query_content():
    item = QueryItem(
        itemId="chat:30",
        kind="chat",
        content="bring gun bring gun bring gun",
        metadata={
            "streamStartSeconds": 49.0,
            "lineNumber": 30,
            "username": "viewer_name",
            "sourceUsername": "viewer_name",
            "analysis": {
                "kind": "userMessage",
                "streamTime": "00:00:49",
                "spans": [
                    {
                        "kind": "repeat",
                        "count": 3,
                        "spans": [{"kind": "text", "text": "bring gun"}],
                    }
                ],
            },
        },
    )

    sections = analysis._evidenceSections(
        transcriptItems=[],
        chatItems=[item],
        includeChat=True,
        chatLayout="interleaved",
    )

    assert sections == [
        "CHRONOLOGICAL EVIDENCE\n"
        "[00:00:49]\n"
        "CHAT viewer_name: (bring gun) ×3"
    ]
    assert item.content == "bring gun bring gun bring gun"


def test_chatPresentation_validation_is_unchanged():
    assert analysis._chatPresentation({}) == (False, "interleaved")
    assert analysis._chatPresentation({"includeChat": True, "chatLayout": "interleaved"}) == (True, "interleaved")
    with pytest.raises(TypeError, match="includeChat"):
        analysis._chatPresentation({"includeChat": 1})
    with pytest.raises(ValueError, match="chatLayout"):
        analysis._chatPresentation({"chatLayout": "mixed"})



def test_chat_query_item_rebuilds_when_persistent_dependency_changes():
    semantic = {
        "address": "evilanalysis/chat/line/40/semantic",
        "dependency": {
            "address": "evilanalysis/chat/line/40/semantic",
            "state": "present",
            "contentSha256": "semantic-a",
            "metadataSha256": "semantic-meta-a",
        },
    }
    bucket = {
        "address": "evilanalysis/chat/second/s50/semantic",
        "dependency": {
            "address": "evilanalysis/chat/second/s50/semantic",
            "state": "present",
            "contentSha256": "bucket-a",
            "metadataSha256": "bucket-meta-a",
        },
        "value": {
            "secondIndex": 50,
            "startSeconds": 50.0,
            "endSeconds": 51.0,
            "members": [
                {
                    "lineNumber": 40,
                    "streamTimeSeconds": 50.0,
                    "semantic": semantic,
                }
            ],
        },
    }
    aggregate = {
        "address": "evilanalysis/chat/second/s50/aggregate",
        "dependency": {
            "address": "evilanalysis/chat/second/s50/aggregate",
            "state": "present",
            "contentSha256": "aggregate-a",
            "metadataSha256": "aggregate-meta-a",
        },
        "value": {
            "secondIndex": 50,
            "secondBucket": {
                "address": bucket["address"],
                "dependency": bucket["dependency"],
            },
            "entries": [
                {
                    "kind": "message",
                    "lineNumber": 40,
                    "semantic": semantic,
                }
            ],
        },
    }
    chat = {
        "sourcePath": "data/chat.txt",
        "records": [
            {
                "lineNumber": 40,
                "channel": "#vedal987",
                "message": "viewer: GIGAEVIL",
                "username": "viewer",
                "body": "GIGAEVIL",
                "timestampText": "2024-03-25 19:18:38",
                "semanticValue": semantic,
                "analysis": {
                    "kind": "userMessage",
                    "includedInText": True,
                    "streamTimeSeconds": 50.0,
                    "streamTime": "00:00:50",
                    "spans": [{"kind": "emote", "name": "GIGAEVIL", "count": 1, "metadata": {}}],
                },
            }
        ],
        "secondBuckets": [bucket],
        "secondAggregates": [aggregate],
    }

    first = analysis._chatQueryItems(chat, previous={})[0]

    same = analysis._chatQueryItems(chat, previous={first.itemId: first})[0]
    assert same is first

    changedBucket = {
        **bucket,
        "dependency": {
            **bucket["dependency"],
            "contentSha256": "bucket-b",
        },
    }
    changedChat = {**chat, "secondBuckets": [changedBucket]}
    rebuilt = analysis._chatQueryItems(
        changedChat,
        previous={first.itemId: first},
    )[0]

    assert rebuilt is not first
    assert rebuilt.itemId == first.itemId
    assert rebuilt.content == first.content
    assert rebuilt.metadata["memory"]["secondBucket"]["dependency"]["contentSha256"] == "bucket-b"



def _persistent_chat_item(
    *,
    line_number: int,
    username: str,
    content: str,
    second: int,
    aggregate_entry: dict[str, object],
) -> QueryItem:
    semantic_address = f"evilanalysis/chat/line/{line_number}/semantic"
    bucket_address = f"evilanalysis/chat/second/s{second}/semantic"
    aggregate_address = f"evilanalysis/chat/second/s{second}/aggregate"
    return QueryItem(
        itemId=f"chat:{line_number}",
        kind="chat",
        content=content,
        metadata={
            "streamStartSeconds": float(second),
            "lineNumber": line_number,
            "username": username,
            "sourceUsername": username,
            "analysis": {
                "kind": "userMessage",
                "streamTime": f"00:00:{second:02d}",
                "spans": [{"kind": "text", "text": content}],
            },
            "memory": {
                "semantic": {
                    "address": semantic_address,
                    "dependency": {
                        "address": semantic_address,
                        "state": "present",
                        "contentSha256": f"semantic-{line_number}",
                        "metadataSha256": f"semantic-meta-{line_number}",
                    },
                },
                "secondBucket": {
                    "address": bucket_address,
                    "dependency": {
                        "address": bucket_address,
                        "state": "present",
                        "contentSha256": f"bucket-{second}",
                        "metadataSha256": f"bucket-meta-{second}",
                    },
                },
                "secondAggregate": {
                    "address": aggregate_address,
                    "dependency": {
                        "address": aggregate_address,
                        "state": "present",
                        "contentSha256": f"aggregate-{second}",
                        "metadataSha256": f"aggregate-meta-{second}",
                    },
                    "entry": aggregate_entry,
                },
            },
        },
    )


def test_persisted_individual_aggregate_entries_override_content_fallback_grouping():
    first = _persistent_chat_item(
        line_number=60,
        username="alice",
        content="same text",
        second=30,
        aggregate_entry={
            "kind": "message",
            "lineNumber": 60,
            "semantic": {"address": "evilanalysis/chat/line/60/semantic"},
        },
    )
    second = _persistent_chat_item(
        line_number=61,
        username="bob",
        content="same text",
        second=30,
        aggregate_entry={
            "kind": "message",
            "lineNumber": 61,
            "semantic": {"address": "evilanalysis/chat/line/61/semantic"},
        },
    )

    sections = analysis._evidenceSections(
        transcriptItems=[],
        chatItems=[first, second],
        includeChat=True,
        chatLayout="interleaved",
    )

    assert sections == [
        "CHRONOLOGICAL EVIDENCE\n"
        "[00:00:30]\n"
        "CHAT alice: same text\n"
        "CHAT bob: same text"
    ]


def test_persisted_identical_message_group_drives_interleaved_compaction():
    entry = {
        "kind": "identicalCanonicalMessage",
        "canonicalMessage": "same text",
        "canonicalSpans": [{"kind": "text", "text": "same text"}],
        "lineNumbers": [62, 63],
        "semantic": [
            {"address": "evilanalysis/chat/line/62/semantic"},
            {"address": "evilanalysis/chat/line/63/semantic"},
        ],
        "sourceUsernames": ["alice", "bob"],
        "messageCount": 2,
        "uniqueSourceUserCount": 2,
    }
    first = _persistent_chat_item(
        line_number=62,
        username="alice",
        content="same text",
        second=31,
        aggregate_entry=entry,
    )
    second = _persistent_chat_item(
        line_number=63,
        username="bob",
        content="same text",
        second=31,
        aggregate_entry=entry,
    )

    sections = analysis._evidenceSections(
        transcriptItems=[],
        chatItems=[first, second],
        includeChat=True,
        chatLayout="interleaved",
    )

    assert sections == [
        "CHRONOLOGICAL EVIDENCE\n"
        "[00:00:31]\n"
        "CHAT: same text ×2 [2 users]"
    ]
