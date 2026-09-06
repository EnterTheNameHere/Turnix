# file: tests/first_party/evilBirthdayAnalysis/test_chatProcessing.py ; version: 4
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "evilBirthdayAnalysis"
    / "packs"
    / "chat"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayChatCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
chat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chat)


class _Io:
    def __init__(self, *, lines: tuple[str, ...] = ()):
        self._lines = lines
        self.readCount = 0

    @property
    def lines(self) -> tuple[str, ...]:
        return self._lines

    @lines.setter
    def lines(self, value: tuple[str, ...]) -> None:
        self._lines = value

    def _payload(self) -> bytes:
        return "\n".join(self._lines).encode("utf-8")

    def observeFile(self, path, *, contentHash=False):
        payload = self._payload()
        return {
            "path": str(path),
            "state": "file",
            "sizeBytes": len(payload),
            "modifiedTimeNs": len(payload) * 1000 + len(self._lines),
            "contentSha256": hashlib.sha256(payload).hexdigest() if contentHash else None,
        }

    def readObservedLines(self, path):
        self.readCount += 1
        payload = self._payload()
        return {
            "value": self._lines,
            "observation": {
                "path": str(path),
                "state": "file",
                "sizeBytes": len(payload),
                "modifiedTimeNs": len(payload) * 1000 + len(self._lines),
                "contentSha256": hashlib.sha256(payload).hexdigest(),
            },
        }

    def readLines(self, _path):
        self.readCount += 1
        return self._lines


class _Ctx:
    def __init__(
        self,
        *,
        lines: tuple[str, ...] = (),
        streamStartTime: str = "00:00:00",
    ):
        self.io = _Io(lines=lines)
        self.config = {
            "chatFile": "chat.txt",
            "chatStartTime": "19:20:00",
            "streamStartTime": streamStartTime,
        }


def _record(line: str, lineNumber: int = 1):
    return chat._parseLine(line, lineNumber)


def test_parseLine_extracts_only_timestamp_channel_and_raw_message_content():
    record = _record("[2024-03-25 20:55:17] #vedal987 jdr1: vedalHeart vedalHeart")

    assert record["timestampText"] == "2024-03-25 20:55:17"
    assert record["channel"] == "#vedal987"
    assert record["message"] == "jdr1: vedalHeart vedalHeart"
    assert "username" not in record
    assert "body" not in record
    assert "analysis" not in record


def test_parseLine_preserves_everything_after_channel_separator_verbatim():
    record = _record("[2024-03-25 20:55:17] #vedal987 Alice:  hello: world  ")

    assert record["message"] == "Alice:  hello: world  "
    assert record["rawLine"] == "[2024-03-25 20:55:17] #vedal987 Alice:  hello: world  "


def test_parseLine_accepts_message_without_username_shape():
    message = "A moderation or information record whose format is not known yet"
    record = _record(f"[2024-03-25 20:55:17] #vedal987 {message}")

    assert record["message"] == message
    assert "username" not in record


def test_parseLine_accepts_empty_message_after_required_separator():
    record = _record("[2024-03-25 20:55:17] #vedal987 ")

    assert record["message"] == ""


@pytest.mark.parametrize(
    "line",
    [
        "",
        "2024-03-25 20:55:17 #vedal987 message",
        "[2024-03-25 20:55:17]  #vedal987 message",
        "[2024-03-25 20:55:17]\t#vedal987 message",
        "[2024-03-25 20:55:17] #vedal987",
        "[2024-03-25 20:55:17] vedal987 message",
    ],
)
def test_parseLine_rejects_invalid_fixed_structure(line: str):
    with pytest.raises(ValueError):
        _record(line)


def test_selector_is_half_open_and_does_not_interpret_records():
    chat._parsedCache.clear()
    lines = (
        "[2024-03-25 19:20:00] #vedal987 first: included",
        "[2024-03-25 19:20:05] #vedal987 an information line without username syntax",
        "[2024-03-25 19:20:10] #vedal987 second: excluded",
    )
    selected = chat._select(_Ctx(lines=lines), {"videoStartSeconds": 0, "videoEndSeconds": 10})

    assert [record["message"] for record in selected["records"]] == [
        "first: included",
        "an information line without username syntax",
    ]
    assert all("username" not in record for record in selected["records"])
    assert all("analysis" not in record for record in selected["records"])


def test_selector_renders_stream_relative_timing_as_derived_source_evidence():
    chat._parsedCache.clear()
    lines = (
        "[2024-03-25 19:28:52] #vedal987 before: pre-stream",
        "[2024-03-25 19:28:53] #vedal987 zero: stream-start",
        "[2024-03-25 19:28:58] #vedal987 after: five-seconds",
    )
    selected = chat._select(
        _Ctx(lines=lines, streamStartTime="00:08:53"),
        {"videoStartSeconds": 532, "videoEndSeconds": 544},
    )

    assert [record["streamTime"] for record in selected["records"]] == [
        "-00:00:01",
        "00:00:00",
        "00:00:05",
    ]
    assert selected["records"][0]["timestampText"] == "2024-03-25 19:28:52"
    assert selected["wallClockAtStreamZero"] == "2024-03-25 19:28:53"


def test_selector_can_return_raw_lookback_without_marking_it_inside_requested_window():
    chat._parsedCache.clear()
    lines = (
        "[2024-03-25 19:19:58] #vedal987 old: outside-lookback",
        "[2024-03-25 19:19:59] #vedal987 prior: semantic-context",
        "[2024-03-25 19:20:00] #vedal987 current: included",
    )
    selected = chat._select(
        _Ctx(lines=lines),
        {"videoStartSeconds": 0, "videoEndSeconds": 1, "lookbackSeconds": 1},
    )

    assert [record["message"] for record in selected["records"]] == [
        "prior: semantic-context",
        "current: included",
    ]
    assert [record["insideRequestedWindow"] for record in selected["records"]] == [False, True]



def test_selector_reuses_parse_cache_while_source_observation_is_unchanged():
    chat._parsedCache.clear()
    ctx = _Ctx(
        lines=(
            "[2024-03-25 19:20:00] #vedal987 first: included",
            "[2024-03-25 19:20:05] #vedal987 second: included",
        )
    )

    first = chat._select(ctx, {"videoStartSeconds": 0, "videoEndSeconds": 10})
    cachedParsed = chat._parsedCache[("chat.txt", "19:20:00")][2]
    second = chat._select(ctx, {"videoStartSeconds": 0, "videoEndSeconds": 10})

    assert ctx.io.readCount == 2
    assert chat._parsedCache[("chat.txt", "19:20:00")][2] is cachedParsed
    assert first["sourceObservation"] == second["sourceObservation"]
    assert first["sourceObservation"]["contentSha256"] is not None


def test_selector_rebuilds_parse_cache_when_source_observation_changes():
    chat._parsedCache.clear()
    ctx = _Ctx(
        lines=(
            "[2024-03-25 19:20:00] #vedal987 first: included",
            "[2024-03-25 19:20:05] #vedal987 second: old",
        )
    )

    first = chat._select(ctx, {"videoStartSeconds": 0, "videoEndSeconds": 10})

    ctx.io.lines = (
        "[2024-03-25 19:20:00] #vedal987 first: included",
        "[2024-03-25 19:20:05] #vedal987 second: changed-value",
    )
    second = chat._select(ctx, {"videoStartSeconds": 0, "videoEndSeconds": 10})

    assert ctx.io.readCount == 2
    assert first["sourceObservation"] != second["sourceObservation"]
    assert [record["message"] for record in second["records"]] == [
        "first: included",
        "second: changed-value",
    ]



def test_selector_can_return_raw_lookahead_without_marking_it_inside_requested_window():
    chat._parsedCache.clear()
    lines = (
        "[2024-03-25 19:20:00] #vedal987 current: included",
        "[2024-03-25 19:20:01] #vedal987 future: semantic-context",
        "[2024-03-25 19:20:02] #vedal987 later: outside-lookahead",
    )
    selected = chat._select(
        _Ctx(lines=lines),
        {
            "videoStartSeconds": 0,
            "videoEndSeconds": 1,
            "lookaheadSeconds": 1,
        },
    )

    assert [record["message"] for record in selected["records"]] == [
        "current: included",
        "future: semantic-context",
    ]
    assert [record["insideRequestedWindow"] for record in selected["records"]] == [
        True,
        False,
    ]
    assert selected["lookaheadSeconds"] == 1.0


def test_selector_context_preserves_half_open_requested_membership():
    chat._parsedCache.clear()
    lines = (
        "[2024-03-25 19:19:59] #vedal987 prior: context",
        "[2024-03-25 19:20:00] #vedal987 start: included",
        "[2024-03-25 19:20:01] #vedal987 end: context",
    )
    selected = chat._select(
        _Ctx(lines=lines),
        {
            "videoStartSeconds": 0,
            "videoEndSeconds": 1,
            "lookbackSeconds": 1,
            "lookaheadSeconds": 1,
        },
    )

    assert [record["insideRequestedWindow"] for record in selected["records"]] == [
        False,
        True,
        False,
    ]
