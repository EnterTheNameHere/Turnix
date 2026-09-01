from __future__ import annotations

import importlib.util
from pathlib import Path


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
