from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path


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


def test_compact_render_omits_date_and_channel_but_keeps_username():
    records = [_record("[2024-03-25 20:23:09] #vedal987 thegrimreapercz: <3 <3 <3 <3 WE LOVE YOU EVIL")]

    rendered = chat._analyzeRecords(records, EMOTES, COMPOSITES)

    assert rendered == ["20:23:09 thegrimreapercz: <3 x4 WE LOVE YOU EVIL"]
    assert records[0]["rawLine"].startswith("[2024-03-25 20:23:09] #vedal987")
