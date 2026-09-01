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
_SPEC = importlib.util.spec_from_file_location("evilBirthdayAnalysisCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
analysis = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(analysis)


class _Capabilities:
    def __init__(self):
        self.calls: list[dict[str, int]] = []

    def call(self, capabilityId, payload):
        assert capabilityId == "evilAnalysis.chat@1"
        self.calls.append(payload)
        startVideo = payload["videoStartSeconds"]
        endVideo = payload["videoEndSeconds"]
        streamStart = startVideo - 533
        streamEnd = endVideo - 533
        text = f"{streamStart}:{streamEnd} viewer: hello"
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
            "records": [
                {"analysis": {"includedInText": True}, "rawLine": "raw included line"},
                {"analysis": {"includedInText": False}, "rawLine": "raw suppressed line"},
            ],
            "text": text,
        }


class _Ctx:
    def __init__(self):
        self.capabilities = _Capabilities()


def test_windowChunks_translate_stream_relative_ranges_to_video_time():
    chunks = analysis._windowChunks(
        positionSeconds=0,
        chunkSeconds=600,
        offsetsSeconds=(0, -600, -1800),
        streamStartVideoSeconds=533,
    )

    assert chunks == [
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
    ]


def test_preparedChatSnapshot_keeps_three_candidate_chunks_without_raw_record_duplication():
    ctx = _Ctx()
    chunks = analysis._windowChunks(
        positionSeconds=0,
        chunkSeconds=600,
        offsetsSeconds=(0, -600, -1800),
        streamStartVideoSeconds=533,
    )
    snapshot = analysis._preparedChatSnapshot(ctx, {"chunks": chunks})

    assert ctx.capabilities.calls == [
        {"videoStartSeconds": 533, "videoEndSeconds": 1133},
        {"videoStartSeconds": -67, "videoEndSeconds": 533},
        {"videoStartSeconds": -1267, "videoEndSeconds": -667},
    ]
    assert snapshot["prepared"] is True
    assert snapshot["includedInPrompt"] is False
    assert [chunk["offsetSeconds"] for chunk in snapshot["chunks"]] == [0, -600, -1800]
    assert [chunk["streamStartSeconds"] for chunk in snapshot["chunks"]] == [0, -600, -1800]
    assert all("records" not in chunk for chunk in snapshot["chunks"])
    assert snapshot["statistics"]["sourceRecordCount"] == 6
    assert snapshot["statistics"]["includedRecordCount"] == 3
    assert snapshot["statistics"]["suppressedRecordCount"] == 3
    assert snapshot["statistics"]["renderedLineCount"] == 3


def test_transcriptQueryItems_create_one_reusable_item_per_timed_segment():
    transcript = {
        "sourcePath": "data/transcript.json",
        "streamStartVideoSeconds": 533.0,
        "segments": [
            {
                "segmentIndex": 12,
                "streamStartSeconds": 45.2,
                "streamStartTime": "00:00:45",
                "words": [
                    {"word": "hello", "start": 45.2, "end": 45.5},
                    {"word": "chat", "start": 45.6, "end": 46.0},
                ],
            },
            {
                "segmentIndex": 13,
                "streamStartSeconds": 48.0,
                "streamStartTime": "00:00:48",
                "words": [{"word": "again", "start": 48.0, "end": 48.4}],
            },
        ],
    }

    items = analysis._transcriptQueryItems(transcript, previous={})

    assert [item.kind for item in items] == ["transcript", "transcript"]
    assert items[0].itemId == "transcript:12:45.2-46.0"
    assert items[0].content == "00:00:45 hello chat"
    assert items[0].metadata["streamStartSeconds"] == 45.2
    assert items[0].metadata["streamEndSeconds"] == 46.0
    assert items[1].metadata["streamStartSeconds"] == 48.0


def test_transcriptQueryItems_reuse_same_absolute_evidence_item():
    transcript = {
        "sourcePath": "data/transcript.json",
        "streamStartVideoSeconds": 533.0,
        "segments": [
            {
                "segmentIndex": 4,
                "streamStartSeconds": 10.0,
                "streamStartTime": "00:00:10",
                "words": [{"word": "same", "start": 10.0, "end": 10.5}],
            }
        ],
    }
    existing = QueryItem(
        itemId="transcript:4:10.0-10.5",
        kind="transcript",
        content="00:00:10 same",
        metadata={"streamStartSeconds": 10.0, "streamEndSeconds": 10.5, "segmentIndex": 4},
    )

    items = analysis._transcriptQueryItems(transcript, previous={existing.itemId: existing})

    assert items == [existing]
    assert items[0] is existing


def test_chatQueryItems_keep_timestamped_internal_identity_but_only_in_chat_kind():
    chat = {
        "sourcePath": "data/chat.txt",
        "records": [
            {
                "lineNumber": 20,
                "username": "viewer_name",
                "message": "GIGAEVIL",
                "timestampText": "2024-03-25 19:18:33",
                "analysis": {
                    "kind": "userMessage",
                    "includedInText": True,
                    "streamTimeSeconds": 45.0,
                    "streamTime": "00:00:45",
                    "spans": [{"kind": "emote", "name": "GIGAEVIL", "count": 1, "metadata": {"reaction": "praise"}}],
                },
            },
            {
                "lineNumber": 21,
                "username": "Fossabot",
                "message": "suppressed",
                "timestampText": "2024-03-25 19:18:34",
                "analysis": {"kind": "botEvent", "includedInText": False, "streamTimeSeconds": 46.0, "streamTime": "00:00:46"},
            },
        ],
    }

    items = analysis._chatQueryItems(chat, previous={})

    assert len(items) == 1
    assert items[0].itemId == "chat:20"
    assert items[0].kind == "chat"
    assert items[0].content == "GIGAEVIL"
    assert items[0].metadata["streamStartSeconds"] == 45.0
    assert items[0].metadata["username"] == "viewer_name"


def test_buildQuery_retrieves_transcript_by_type_and_orders_it_by_stream_time_while_chat_stays_out():
    items = [
        QueryItem(itemId="context", kind="context", content="context"),
        QueryItem(itemId="profile", kind="analysis-profile", content="profile"),
        QueryItem(itemId="prompt", kind="prompt", content="prompt"),
        QueryItem(itemId="t2", kind="transcript", content="00:00:48 second", metadata={"streamStartSeconds": 48.0}),
        QueryItem(itemId="chat:20", kind="chat", content="GIGAEVIL", metadata={"streamStartSeconds": 45.0, "username": "viewer_name"}),
        QueryItem(itemId="t1", kind="transcript", content="00:00:45 first", metadata={"streamStartSeconds": 45.0}),
    ]
    payload = {
        "input": {
            "profile": {"name": "default"},
            "promptName": "main",
            "windowIndex": 0,
            "window": {"positionSeconds": 0},
        },
        "queryItems": [item.snapshot() for item in items],
    }

    query = analysis._buildQuery(None, payload)

    assert query["payload"] == (
        "CONTEXT\ncontext\n\n"
        "ANALYSIS PROFILE\nprofile\n\n"
        "ANALYSIS INSTRUCTION\nprompt\n\n"
        "TRANSCRIPT WINDOW\n00:00:45 first\n00:00:48 second"
    )
    assert "GIGAEVIL" not in query["payload"]
    assert "viewer_name" not in query["payload"]
    assert query["metadata"]["chatIncluded"] is False
