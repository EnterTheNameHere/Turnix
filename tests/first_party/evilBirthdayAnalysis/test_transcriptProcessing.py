from __future__ import annotations

import importlib.util
from pathlib import Path


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "evilBirthdayAnalysis"
    / "packs"
    / "transcript"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayTranscriptCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
transcript = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(transcript)


class _Io:
    def __init__(self, source: dict[str, object]):
        self._source = source

    def readJson(self, _path):
        return self._source


class _Ctx:
    def __init__(self, source: dict[str, object]):
        self.io = _Io(source)
        self.config = {
            "transcriptFile": "transcript.json",
            "streamStartTime": "00:08:53",
        }


def test_selector_renders_one_stream_relative_timestamp_per_retained_segment():
    transcript._sourceCache.clear()
    source = {
        "segments": [
            {
                "words": [
                    {"word": "First", "start": 1.25, "end": 1.50},
                    {"word": "segment", "start": 1.55, "end": 2.10},
                ]
            },
            {
                "words": [
                    {"word": "Second", "start": 5.95, "end": 6.20},
                    {"word": "one", "start": 6.25, "end": 6.50},
                ]
            },
        ]
    }

    selected = transcript._select(
        _Ctx(source),
        {"videoStartSeconds": 533, "videoEndSeconds": 901},
    )

    assert selected["text"] == "00:00:01 First segment\n00:00:05 Second one"
    assert selected["segments"][0]["streamStartSeconds"] == 1.25
    assert selected["segments"][0]["streamStartTime"] == "00:00:01"
    assert selected["segments"][1]["streamStartSeconds"] == 5.95
    assert selected["streamStartVideoSeconds"] == 533.0


def test_selector_uses_first_retained_word_when_window_starts_inside_segment():
    transcript._sourceCache.clear()
    source = {
        "segments": [
            {
                "words": [
                    {"word": "before", "start": 1.0, "end": 1.5},
                    {"word": "inside", "start": 2.1, "end": 2.5},
                    {"word": "window", "start": 2.6, "end": 3.0},
                ]
            }
        ]
    }

    selected = transcript._select(
        _Ctx(source),
        {"videoStartSeconds": 535, "videoEndSeconds": 537},
    )

    assert selected["text"] == "00:00:02 inside window"
    assert selected["segments"][0]["streamStartSeconds"] == 2.1


def test_selector_returns_empty_for_chunk_wholly_before_transcript_start():
    transcript._sourceCache.clear()
    source = {
        "segments": [
            {
                "words": [
                    {"word": "first", "start": 0.0, "end": 0.5},
                    {"word": "words", "start": 0.6, "end": 1.0},
                ]
            }
        ]
    }

    selected = transcript._select(
        _Ctx(source),
        {"videoStartSeconds": -67, "videoEndSeconds": 533},
    )

    assert selected["transcriptStartSeconds"] == -600.0
    assert selected["transcriptEndSeconds"] == 0.0
    assert selected["segments"] == []
    assert selected["text"] == ""


def test_stream_time_formatter_floors_fractional_seconds_and_supports_negative_values():
    assert transcript._formatStreamTime(12.999) == "00:00:12"
    assert transcript._formatStreamTime(-0.001) == "-00:00:01"
