# file: tests/first_party/evilBirthdayAnalysis/test_chatPetpetComposition.py ; version: 1
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
_SPEC = importlib.util.spec_from_file_location("evilBirthdayChatSemanticsPetpet", _CODE_ENTRY)
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


def _fixedComposite(*tokens: str) -> dict[str, object]:
    return {
        "tokens": tuple(tokens),
        "foldedTokens": tuple(token.casefold() for token in tokens),
        "metadata": {"semanticClass": "testFixedComposite"},
    }


def test_petpet_binds_to_immediately_preceding_recognized_emote_as_one_composite():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage("FOCUS PETPET", emotes, [])

    assert spans == [
        {
            "kind": "composite",
            "tokens": ["FOCUS", "PETPET"],
            "count": 1,
            "metadata": {
                "composition": "postfixOverlay",
                "modifier": "PETPET",
                "baseEmote": "FOCUS",
            },
        }
    ]
    evaluated = chatSemantics._evaluate(None, {"spans": spans})
    assert evaluated["lexicallyComplete"] is True
    assert evaluated["semanticallyComplete"] is False


def test_petpet_binding_is_case_insensitive_and_preserves_canonical_base_name():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage("focus petpet", emotes, [])

    assert spans[0]["tokens"] == ["FOCUS", "PETPET"]
    assert spans[0]["metadata"]["baseEmote"] == "FOCUS"


def test_repeated_petpet_composites_collapse_as_the_same_reaction_unit():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage(
        "FOCUS PETPET FOCUS PETPET",
        emotes,
        [],
    )

    assert len(spans) == 1
    assert spans[0]["kind"] == "composite"
    assert spans[0]["tokens"] == ["FOCUS", "PETPET"]
    assert spans[0]["count"] == 2


def test_petpet_composite_accepts_explicit_multiplier_after_modifier():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage("FOCUS PETPET x3", emotes, [])

    assert spans[0]["tokens"] == ["FOCUS", "PETPET"]
    assert spans[0]["count"] == 3


def test_unbound_petpet_stays_normal_text_and_reports_missing_base_warning():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage("PETPET", emotes, [])
    warnings = chatSemantics._petpetWarnings("PETPET", emotes)

    assert spans == [{"kind": "text", "text": "PETPET"}]
    assert chatSemantics._evaluate(None, {"spans": spans})["lexicallyComplete"] is False
    assert warnings == [
        {
            "code": "unboundPetpet",
            "token": "PETPET",
            "sourceToken": "PETPET",
            "tokenIndex": 0,
            "precedingToken": None,
            "message": (
                "PETPET is not immediately preceded by a recognized emote; "
                "the preceding base may be missing from chatEmotes.json."
            ),
        }
    ]


def test_petpet_after_unknown_base_stays_normal_message_and_warns_about_preceding_token():
    emotes = _emotes("FOCUS")
    message = "missingEmote PETPET"

    spans = chatSemantics._lexMessage(message, emotes, [])
    warnings = chatSemantics._petpetWarnings(message, emotes)

    assert spans == [{"kind": "text", "text": message}]
    assert warnings[0]["code"] == "unboundPetpet"
    assert warnings[0]["precedingToken"] == "missingEmote"


def test_second_petpet_after_valid_composition_is_unbound_and_prevents_reaction_only_classification():
    emotes = _emotes("FOCUS")
    message = "FOCUS PETPET PETPET"

    spans = chatSemantics._lexMessage(message, emotes, [])
    warnings = chatSemantics._petpetWarnings(message, emotes)

    assert spans[0]["kind"] == "composite"
    assert spans[0]["tokens"] == ["FOCUS", "PETPET"]
    assert spans[1] == {"kind": "text", "text": "PETPET"}
    assert warnings[0]["precedingToken"] == "PETPET"
    assert chatSemantics._evaluate(None, {"spans": spans})["lexicallyComplete"] is False


def test_petpet_postfix_binding_is_tighter_than_fixed_composite_matching():
    emotes = _emotes("ReallyGunPull", "Tutel")
    composites = [_fixedComposite("ReallyGunPull", "Tutel")]

    spans = chatSemantics._lexMessage(
        "ReallyGunPull Tutel PETPET",
        emotes,
        composites,
    )

    assert spans[0]["kind"] == "emote"
    assert spans[0]["name"] == "ReallyGunPull"
    assert spans[1]["kind"] == "composite"
    assert spans[1]["tokens"] == ["Tutel", "PETPET"]
    assert chatSemantics._petpetWarnings(
        "ReallyGunPull Tutel PETPET",
        emotes,
    ) == []


def test_line_semantic_persists_unbound_petpet_warning_while_retaining_normal_message():
    emotes = _emotes("FOCUS")

    semantic = chatSemantics._lineSemantic(
        "viewer: missingEmote PETPET",
        emotes=emotes,
        composites=[],
    )

    assert semantic["kind"] == "userMessage"
    assert semantic["body"] == "missingEmote PETPET"
    assert semantic["spans"] == [{"kind": "text", "text": "missingEmote PETPET"}]
    assert semantic["warnings"][0]["code"] == "unboundPetpet"
    assert semantic["warnings"][0]["precedingToken"] == "missingEmote"
