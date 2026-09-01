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
    def call(self, capabilityId, payload):
        assert capabilityId == "evilAnalysis.chat@1"
        assert payload == {"videoStartSeconds": -1800, "videoEndSeconds": 2400}
        return {
            "sourcePath": "data/chat.txt",
            "chatStartTime": "19:08:55",
            "streamStartTime": "00:08:53",
            "streamStartVideoSeconds": 533.0,
            "wallClockAtMediaZero": "2024-03-25 19:08:55",
            "wallClockAtStreamZero": "2024-03-25 19:17:48",
            "videoStartSeconds": -1800.0,
            "videoEndSeconds": 2400.0,
            "streamStartSeconds": -2333.0,
            "streamEndSeconds": 1867.0,
            "startWallClock": "2024-03-25 18:38:55",
            "endWallClock": "2024-03-25 19:48:55",
            "records": [
                {"analysis": {"includedInText": True}, "rawLine": "raw included line"},
                {"analysis": {"includedInText": False}, "rawLine": "raw suppressed line"},
            ],
            "text": "00:00:01 viewer: hello",
        }


class _Ctx:
    capabilities = _Capabilities()


def test_preparedChatSnapshot_keeps_candidate_text_without_raw_record_duplication():
    snapshot = analysis._preparedChatSnapshot(
        _Ctx(),
        {
            "chatStartSeconds": -1800,
            "chatEndSeconds": 2400,
        },
    )

    assert snapshot["prepared"] is True
    assert snapshot["includedInPrompt"] is False
    assert snapshot["text"] == "00:00:01 viewer: hello"
    assert "records" not in snapshot
    assert snapshot["metadata"]["streamStartSeconds"] == -2333.0
    assert snapshot["statistics"] == {
        "sourceRecordCount": 2,
        "includedRecordCount": 1,
        "suppressedRecordCount": 1,
        "renderedLineCount": 1,
        "characterCount": 23,
        "utf8ByteCount": 23,
    }
