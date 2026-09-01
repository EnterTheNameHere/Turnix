from __future__ import annotations

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


EMOTES = {
    "vedalHeart": {"semanticClass": "affection", "classificationSource": "userDefined"},
    "<3": {"semanticClass": "affection", "classificationSource": "userDefined"},
    "evilCry": {"semanticClass": "sadness", "classificationSource": "userDefined"},
    "Clap": {},
    "ReallyGunPull": {"semanticClass": "negative", "target": "Vedal", "classificationSource": "userDefined"},
    "Tutel": {"entity": "Vedal", "classificationSource": "userDefined"},
    "AnnyLebronJam": {"entity": "Anny", "classificationSource": "userDefined"},
}
COMPOSITES = [
    {
        "tokens": ("ReallyGunPull", "Tutel"),
        "metadata": {"semanticClass": "negative", "target": "Vedal", "classificationSource": "userDefined"},
    },
    {
        "tokens": ("ReallyGunPull", "AnnyLebronJam"),
        "metadata": {"semanticClass": "negative", "target": "Anny", "classificationSource": "userDefined"},
    },
]


class _Io:
    def __init__(self, *, lines: tuple[str, ...] = (), vocabulary: dict[str, object] | None = None):
        self._lines = lines
        self._vocabulary = vocabulary or {"emotes": EMOTES, "composites": []}

    def readLines(self, _path):
        return self._lines

    def readJson(self, _path):
        return self._vocabulary


class _Ctx:
    def __init__(
        self,
        *,
        lines: tuple[str, ...] = (),
        vocabulary: dict[str, object] | None = None,
        streamStartTime: str = "00:00:00",
    ):
        self.io = _Io(lines=lines, vocabulary=vocabulary)
        self.config = {
            "chatFile": "chat.txt",
            "chatStartTime": "19:20:00",
            "chatEmotesFile": "chatEmotes.json",
            "streamStartTime": streamStartTime,
        }


def _record(line: str, lineNumber: int = 1):
    return chat._parseLine(line, lineNumber)


def test_parseLine_retains_source_but_separates_user_and_message():
    record = _record("[2024-03-25 20:55:17] #vedal987 jdr1: vedalHeart vedalHeart")

    assert record["timestampText"] == "2024-03-25 20:55:17"
    assert record["timeText"] == "20:55:17"
    assert record["channel"] == "#vedal987"
    assert record["username"] == "jdr1"
    assert record["message"] == "vedalHeart vedalHeart"
    assert record["rawLine"] == "[2024-03-25 20:55:17] #vedal987 jdr1: vedalHeart vedalHeart"


def test_lexMessage_collapses_repeated_emotes_and_explicit_multiplier():
    repeated = chat._lexMessage("vedalHeart vedalHeart vedalHeart vedalHeart vedalHeart", EMOTES, COMPOSITES)
    explicit = chat._lexMessage("vedalHeart x5", EMOTES, COMPOSITES)

    assert repeated == [
        {
            "kind": "emote",
            "name": "vedalHeart",
            "count": 5,
            "metadata": {"semanticClass": "affection", "classificationSource": "userDefined"},
        }
    ]
    assert explicit == repeated
    assert chat._renderSpans(repeated) == "vedalHeart x5"


def test_explicit_multiplier_applies_to_occurrence_before_adjacent_merge():
    spans = chat._lexMessage("vedalHeart x2 vedalHeart x2", EMOTES, COMPOSITES)
    mixed = chat._lexMessage("vedalHeart vedalHeart x2", EMOTES, COMPOSITES)

    assert spans[0]["count"] == 4
    assert mixed[0]["count"] == 3


def test_lexMessage_preserves_mixed_order_and_confirmed_composite():
    spans = chat._lexMessage("ReallyGunPull Tutel COME TO HER PARTY", EMOTES, COMPOSITES)

    assert spans[0]["kind"] == "composite"
    assert spans[0]["tokens"] == ["ReallyGunPull", "Tutel"]
    assert spans[0]["metadata"]["target"] == "Vedal"
    assert spans[1] == {"kind": "text", "text": "COME TO HER PARTY"}
    assert chat._renderSpans(spans) == "ReallyGunPull Tutel COME TO HER PARTY"


def test_lexMessage_collapses_whole_repeated_mixed_chant():
    message = " ".join(["ReallyGunPull Tutel COME TO HER PARTY"] * 4)
    spans = chat._lexMessage(message, EMOTES, COMPOSITES)

    assert len(spans) == 1
    assert spans[0]["kind"] == "repeat"
    assert spans[0]["count"] == 4
    assert chat._renderSpans(spans) == "(ReallyGunPull Tutel COME TO HER PARTY) x4"


def test_mixed_emotes_remain_distinct_without_confirmed_composite():
    spans = chat._lexMessage("evilCry Clap", EMOTES, COMPOSITES)

    assert [span["kind"] for span in spans] == ["emote", "emote"]
    assert [span["name"] for span in spans] == ["evilCry", "Clap"]


def test_user_command_is_preserved_as_user_signal():
    spans = chat._lexMessage("!abandonedarchive", EMOTES, COMPOSITES)

    assert spans == [{"kind": "command", "command": "abandonedarchive", "arguments": []}]
    assert chat._renderSpans(spans) == "!abandonedarchive"


def test_generated_gift_batch_collapses_named_followups():
    records = [
        _record(
            "[2024-03-25 19:09:17] #vedal987 mybraza: mybraza is gifting 2 Tier 1 Subs to vedal987's community! They've gifted a total of 126 in the channel!",
            1,
        ),
        _record("[2024-03-25 19:09:18] #vedal987 viewer: HAPPY BIRTHDAY", 2),
        _record("[2024-03-25 19:09:18] #vedal987 mybraza: mybraza gifted a Tier 1 sub to Aemable!", 3),
        _record("[2024-03-25 19:09:19] #vedal987 mybraza: mybraza gifted a Tier 1 sub to OtherUser!", 4),
    ]

    rendered = chat._analyzeRecords(records, EMOTES, COMPOSITES)

    assert rendered == [
        "19:09:17 mybraza: [gift 2xT1; total 126]",
        "19:09:18 viewer: HAPPY BIRTHDAY",
    ]
    batch = records[0]["analysis"]["event"]
    assert batch["recipients"] == ["Aemable", "OtherUser"]
    assert records[2]["analysis"]["includedInText"] is False
    assert records[3]["analysis"]["includedInText"] is False


def test_selector_reconstructs_gift_batch_from_pre_window_lookback():
    chat._parsedCache.clear()
    lines = (
        "[2024-03-25 19:19:59] #vedal987 mybraza: mybraza is gifting 2 Tier 1 Subs to vedal987's community! They've gifted a total of 126 in the channel!",
        "[2024-03-25 19:20:00] #vedal987 mybraza: mybraza gifted a Tier 1 sub to Aemable!",
        "[2024-03-25 19:20:01] #vedal987 viewer: HAPPY BIRTHDAY",
        "[2024-03-25 19:20:02] #vedal987 mybraza: mybraza gifted a Tier 1 sub to OtherUser!",
        "[2024-03-25 19:20:10] #vedal987 viewer2: hello",
    )
    ctx = _Ctx(lines=lines)

    selected = chat._select(ctx, {"videoStartSeconds": 0, "videoEndSeconds": 10})

    assert selected["text"] == "00:00:01 viewer: HAPPY BIRTHDAY"
    assert [record["lineNumber"] for record in selected["records"]] == [2, 3, 4]
    assert selected["records"][0]["analysis"]["includedInText"] is False
    assert selected["records"][2]["analysis"]["includedInText"] is False
    assert all(record["lineNumber"] != 1 for record in selected["records"])


def test_selector_is_half_open_at_end_boundary():
    chat._parsedCache.clear()
    lines = (
        "[2024-03-25 19:20:00] #vedal987 first: included",
        "[2024-03-25 19:20:10] #vedal987 second: excluded",
    )
    selected = chat._select(_Ctx(lines=lines), {"videoStartSeconds": 0, "videoEndSeconds": 10})

    assert [record["username"] for record in selected["records"]] == ["first"]
    assert selected["text"] == "00:00:00 first: included"


def test_selector_renders_stream_relative_time_and_preserves_wall_clock_source():
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

    assert selected["text"] == (
        "-00:00:01 before: pre-stream\n"
        "00:00:00 zero: stream-start\n"
        "00:00:05 after: five-seconds"
    )
    assert selected["records"][0]["timestampText"] == "2024-03-25 19:28:52"
    assert selected["records"][0]["analysis"]["streamTime"] == "-00:00:01"
    assert selected["wallClockAtStreamZero"] == "2024-03-25 19:28:53"


def test_known_fossabot_automation_is_retained_but_not_rendered():
    records = [
        _record("[2024-03-25 19:30:09] #vedal987 fossabot: @RatK1ngg_, Your message is too long [warning]", 1),
        _record("[2024-03-25 19:30:24] #vedal987 fossabot: Neuro-sama Headquarters: https://discord.gg/neurosama", 2),
        _record("[2024-03-25 19:30:25] #vedal987 human: hello", 3),
    ]

    rendered = chat._analyzeRecords(records, EMOTES, COMPOSITES)

    assert rendered == ["19:30:25 human: hello"]
    assert records[0]["analysis"]["kind"] == "botEvent"
    assert records[1]["analysis"]["kind"] == "botEvent"
    assert records[0]["analysis"]["includedInText"] is False


def test_unknown_fossabot_message_survives_as_user_message():
    records = [_record("[2024-03-25 19:30:25] #vedal987 fossabot: an unfamiliar future message")]

    rendered = chat._analyzeRecords(records, EMOTES, COMPOSITES)

    assert rendered == ["19:30:25 fossabot: an unfamiliar future message"]
    assert records[0]["analysis"]["kind"] == "userMessage"


def test_compact_render_omits_date_and_channel_but_keeps_username():
    records = [_record("[2024-03-25 20:23:09] #vedal987 thegrimreapercz: <3 <3 <3 <3 WE LOVE YOU EVIL")]

    rendered = chat._analyzeRecords(records, EMOTES, COMPOSITES)

    assert rendered == ["20:23:09 thegrimreapercz: <3 x4 WE LOVE YOU EVIL"]
    assert records[0]["rawLine"].startswith("[2024-03-25 20:23:09] #vedal987")


def test_vocabulary_rejects_unknown_composite_tokens():
    ctx = _Ctx(
        vocabulary={
            "emotes": {"Tutel": {}},
            "composites": [{"tokens": ["ReallyGunPul", "Tutel"]}],
        }
    )

    with pytest.raises(ValueError, match="unknown emote"):
        chat._vocabulary(ctx)


def test_vocabulary_rejects_duplicate_composite_patterns():
    ctx = _Ctx(
        vocabulary={
            "emotes": {"ReallyGunPull": {}, "Tutel": {}},
            "composites": [
                {"tokens": ["ReallyGunPull", "Tutel"]},
                {"tokens": ["ReallyGunPull", "Tutel"]},
            ],
        }
    )

    with pytest.raises(ValueError, match="Duplicate chat composite"):
        chat._vocabulary(ctx)
