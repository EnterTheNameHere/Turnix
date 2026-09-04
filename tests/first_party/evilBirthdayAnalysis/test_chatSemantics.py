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
    / "chatSemantics"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayChatSemanticsCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
chatSemantics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chatSemantics)


EMOTES = {
    "GIGAEVIL": {"semanticClass": "praise", "classificationSource": "userDefined"},
    "ReallyGunPull": {"semanticClass": "negative", "target": "Vedal", "classificationSource": "userDefined"},
    "Tutel": {"entity": "Vedal", "classificationSource": "userDefined"},
    "Clap": {},
}
COMPOSITES = [
    {
        "tokens": ["ReallyGunPull", "Tutel"],
        "semanticClass": "negative",
        "target": "Vedal",
        "classificationSource": "userDefined",
    }
]


class _Io:
    def readJson(self, _path):
        return {"emotes": EMOTES, "composites": COMPOSITES}


class _Ctx:
    def __init__(self):
        self.io = _Io()
        self.config = {"chatEmotesFile": "chatEmotes.json"}


def _evaluate(spans):
    return chatSemantics._evaluate(None, {"spans": spans})


def _raw(
    lineNumber: int,
    message: str,
    *,
    streamTimeSeconds: float,
    streamTime: str,
    insideRequestedWindow: bool = True,
) -> dict[str, object]:
    return {
        "lineNumber": lineNumber,
        "channel": "#vedal987",
        "message": message,
        "timestampText": "2024-03-25 19:20:00",
        "rawLine": f"[2024-03-25 19:20:00] #vedal987 {message}",
        "streamTimeSeconds": streamTimeSeconds,
        "streamTime": streamTime,
        "insideRequestedWindow": insideRequestedWindow,
    }


def test_known_user_defined_emote_is_fully_reducible_and_aggregation_eligible():
    result = _evaluate(
        [
            {
                "kind": "emote",
                "name": "GIGAEVIL",
                "count": 3,
                "metadata": {"semanticClass": "praise", "classificationSource": "userDefined"},
            }
        ]
    )

    assert result["lexicallyComplete"] is True
    assert result["semanticallyComplete"] is True
    assert result["aggregationEligible"] is True
    assert result["semanticUnits"] == [
        {
            "meaning": {"semanticClass": "praise", "classificationSource": "userDefined"},
            "count": 3,
        }
    ]


def test_recognized_but_unclassified_emote_is_lexical_not_semantic():
    result = _evaluate([{"kind": "emote", "name": "Clap", "count": 2, "metadata": {}}])

    assert result["lexicallyComplete"] is True
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False
    assert result["semanticUnits"] == []


def test_residual_text_prevents_semantic_flattening():
    result = _evaluate(
        [
            {
                "kind": "emote",
                "name": "GIGAEVIL",
                "count": 1,
                "metadata": {"semanticClass": "praise", "classificationSource": "userDefined"},
            },
            {"kind": "text", "text": "holy shit she actually did it"},
        ]
    )

    assert result["lexicallyComplete"] is False
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False


def test_exact_repeat_multiplies_semantic_occurrence_count():
    result = _evaluate(
        [
            {
                "kind": "repeat",
                "count": 5,
                "spans": [
                    {
                        "kind": "emote",
                        "name": "GIGAEVIL",
                        "count": 1,
                        "metadata": {"semanticClass": "praise", "classificationSource": "userDefined"},
                    }
                ],
            }
        ]
    )

    assert result["structurallyCompressed"] is True
    assert result["semanticUnits"][0]["count"] == 5


def test_non_user_defined_semantic_suggestion_is_not_aggregation_eligible():
    result = _evaluate(
        [
            {
                "kind": "emote",
                "name": "futureEmote",
                "count": 1,
                "metadata": {"semanticClass": "praise", "classificationSource": "llmClusterSuggested"},
            }
        ]
    )

    assert result["lexicallyComplete"] is True
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False


def test_invalid_repeat_shape_fails_closed():
    with pytest.raises(ValueError, match="count greater than one"):
        _evaluate([{"kind": "repeat", "count": 1, "spans": [{"kind": "text", "text": "x"}]}])


def test_interpret_dynamically_recognizes_user_shape_and_keeps_raw_message():
    result = chatSemantics._interpret(
        _Ctx(),
        {
            "records": [
                _raw(1, "viewer: GIGAEVIL GIGAEVIL", streamTimeSeconds=5.0, streamTime="00:00:05")
            ]
        },
    )

    record = result["records"][0]
    assert record["message"] == "viewer: GIGAEVIL GIGAEVIL"
    assert record["username"] == "viewer"
    assert record["body"] == "GIGAEVIL GIGAEVIL"
    assert record["analysis"]["kind"] == "userMessage"
    assert record["analysis"]["spans"][0]["kind"] == "emote"
    assert record["analysis"]["spans"][0]["count"] == 2
    assert result["text"] == "00:00:05 viewer: GIGAEVIL x2"


def test_interpret_preserves_unknown_message_without_guessing_username_or_body():
    rawMessage = "A moderation or information form not understood by this CodeEntry"
    result = chatSemantics._interpret(
        _Ctx(),
        {"records": [_raw(1, rawMessage, streamTimeSeconds=7.0, streamTime="00:00:07")]},
    )

    record = result["records"][0]
    assert record["message"] == rawMessage
    assert "username" not in record
    assert "body" not in record
    assert record["analysis"]["kind"] == "unknownMessage"
    assert record["analysis"]["rawMessage"] == rawMessage
    assert record["analysis"]["includedInText"] is True
    assert result["text"] == f"00:00:07 [unclassified] {rawMessage}"


def test_interpret_uses_pre_window_raw_context_to_reconstruct_gift_batch():
    result = chatSemantics._interpret(
        _Ctx(),
        {
            "records": [
                _raw(
                    1,
                    "mybraza: mybraza is gifting 2 Tier 1 Subs to vedal987's community! They've gifted a total of 126 in the channel!",
                    streamTimeSeconds=-1.0,
                    streamTime="-00:00:01",
                    insideRequestedWindow=False,
                ),
                _raw(
                    2,
                    "mybraza: mybraza gifted a Tier 1 sub to Aemable!",
                    streamTimeSeconds=0.0,
                    streamTime="00:00:00",
                ),
                _raw(
                    3,
                    "viewer: HAPPY BIRTHDAY",
                    streamTimeSeconds=1.0,
                    streamTime="00:00:01",
                ),
                _raw(
                    4,
                    "mybraza: mybraza gifted a Tier 1 sub to OtherUser!",
                    streamTimeSeconds=2.0,
                    streamTime="00:00:02",
                ),
            ]
        },
    )

    assert result["records"][0]["analysis"]["includedInText"] is False
    assert result["records"][1]["analysis"]["includedInText"] is False
    assert result["records"][3]["analysis"]["includedInText"] is False
    assert result["records"][1]["analysis"]["partOfGiftBatchLineNumber"] == 1
    assert result["records"][3]["analysis"]["partOfGiftBatchLineNumber"] == 1
    assert result["records"][0]["analysis"]["event"]["recipients"] == ["Aemable", "OtherUser"]
    assert result["text"] == "00:00:01 viewer: HAPPY BIRTHDAY"


def test_interpret_known_fossabot_automation_is_retained_but_suppressed():
    result = chatSemantics._interpret(
        _Ctx(),
        {
            "records": [
                _raw(
                    1,
                    "fossabot: @RatK1ngg_, Your message is too long [warning]",
                    streamTimeSeconds=9.0,
                    streamTime="00:00:09",
                )
            ]
        },
    )

    record = result["records"][0]
    assert record["analysis"]["kind"] == "botEvent"
    assert record["analysis"]["includedInText"] is False
    assert result["text"] == ""


def test_interpret_command_and_confirmed_composite_behavior_remains_explicit():
    result = chatSemantics._interpret(
        _Ctx(),
        {
            "records": [
                _raw(
                    1,
                    "viewer: !clip now",
                    streamTimeSeconds=11.0,
                    streamTime="00:00:11",
                ),
                _raw(
                    2,
                    "viewer: ReallyGunPull Tutel ReallyGunPull Tutel",
                    streamTimeSeconds=12.0,
                    streamTime="00:00:12",
                ),
            ]
        },
    )

    command = result["records"][0]["analysis"]["spans"]
    composite = result["records"][1]["analysis"]["spans"]

    assert command == [{"kind": "command", "command": "clip", "arguments": ["now"]}]
    assert composite == [
        {
            "kind": "composite",
            "tokens": ["ReallyGunPull", "Tutel"],
            "count": 2,
            "metadata": {
                "semanticClass": "negative",
                "target": "Vedal",
                "classificationSource": "userDefined",
            },
        }
    ]
    assert result["text"].splitlines() == [
        "00:00:11 viewer: !clip now",
        "00:00:12 viewer: ReallyGunPull Tutel x2",
    ]


def test_interpret_unknown_fossabot_message_remains_user_message():
    result = chatSemantics._interpret(
        _Ctx(),
        {
            "records": [
                _raw(
                    1,
                    "fossabot: an unfamiliar future message",
                    streamTimeSeconds=10.0,
                    streamTime="00:00:10",
                )
            ]
        },
    )

    assert result["records"][0]["analysis"]["kind"] == "userMessage"
    assert result["text"] == "00:00:10 fossabot: an unfamiliar future message"
