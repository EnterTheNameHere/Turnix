# file: tests/first_party/evilBirthdayAnalysis/test_chatSemanticSanitization.py ; version: 1
from __future__ import annotations

import importlib.util
from pathlib import Path


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "evilBirthdayAnalysis"
    / "packs"
    / "chatSemantics"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayChatSemanticSanitization", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
chatSemantics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chatSemantics)


def _emotes(*names: str) -> dict[str, dict[str, object]]:
    return {
        name.casefold(): {
            "name": name,
            "metadata": {},
        }
        for name in names
    }


def test_trailing_unicode_tag_is_removed_before_emote_lexing():
    rawMessage = "viewer: Stronge \U000e0000"

    semantic = chatSemantics._lineSemantic(
        rawMessage,
        emotes=_emotes("Stronge"),
        composites=[],
    )

    assert rawMessage.endswith(" \U000e0000")
    assert semantic["body"] == "Stronge"
    assert semantic["spans"] == [
        {
            "kind": "emote",
            "name": "Stronge",
            "count": 1,
            "metadata": {},
        }
    ]


def test_trailing_unicode_tag_does_not_break_repeated_emote_only_message():
    unit = "FeelsBirthdayMan Clap eliv EDM"
    rawMessage = f"viewer: {unit} {unit} \U000e0000"

    semantic = chatSemantics._lineSemantic(
        rawMessage,
        emotes=_emotes("FeelsBirthdayMan", "Clap", "eliv", "EDM"),
        composites=[],
    )

    assert semantic["body"] == f"{unit} {unit}"
    assert semantic["spans"] == [
        {
            "kind": "repeat",
            "count": 2,
            "spans": [
                {"kind": "emote", "name": "FeelsBirthdayMan", "count": 1, "metadata": {}},
                {"kind": "emote", "name": "Clap", "count": 1, "metadata": {}},
                {"kind": "emote", "name": "eliv", "count": 1, "metadata": {}},
                {"kind": "emote", "name": "EDM", "count": 1, "metadata": {}},
            ],
        }
    ]


def test_unicode_tag_inside_authored_content_is_not_deleted():
    value = "before\U000e0000after"

    assert chatSemantics._sanitizeSemanticBody(value) == value
