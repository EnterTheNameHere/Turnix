# file: tests/first_party/evilBirthdayAnalysis/test_chatPetpetComposition.py ; version: 2
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "evilBirthdayAnalysis"
    / "packs"
    / "chatSemantics"
    / "entry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayChatSemanticsModifiers", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
chatSemantics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chatSemantics)


def _modifierDefinitions(*names: str) -> dict[str, dict[str, object]]:
    return {
        name.casefold(): {
            "name": name,
            "kind": "postfixEmote",
            "metadata": {},
        }
        for name in names
    }


def _emotes(
    *names: str,
    modifiers: tuple[str, ...] = ("PETPET",),
) -> dict[str, dict[str, object]]:
    values = {
        name.casefold(): {
            "name": name,
            "metadata": {},
        }
        for name in names
    }
    return chatSemantics._EmoteVocabulary(
        values,
        postfixModifiers=_modifierDefinitions(*modifiers),
    )


def _fixedComposite(*tokens: str) -> dict[str, object]:
    return {
        "tokens": tuple(tokens),
        "foldedTokens": tuple(token.casefold() for token in tokens),
        "metadata": {"semanticClass": "testFixedComposite"},
    }


def test_vocabulary_reads_multiple_postfix_modifiers_from_chat_emotes_config():
    class _Io:
        @staticmethod
        def readObservedJson(_path):
            return {
                "value": {
                    "emotes": {"FOCUS": {}},
                    "modifiers": {
                        "PETPET": {"kind": "postfixEmote"},
                        "HEADPAT": {"kind": "postfixEmote"},
                    },
                    "composites": [],
                },
                "observation": {"contentSha256": "test-vocabulary"},
            }

    ctx = SimpleNamespace(config={"chatEmotesFile": "chatEmotes.json"}, io=_Io())

    emotes, composites, observation = chatSemantics._vocabulary(ctx)

    assert composites == []
    assert observation["contentSha256"] == "test-vocabulary"
    assert set(emotes.postfixModifiers) == {"petpet", "headpat"}
    assert emotes.postfixModifiers["petpet"]["name"] == "PETPET"
    assert emotes.postfixModifiers["headpat"]["name"] == "HEADPAT"


def test_petpet_binds_to_immediately_preceding_recognized_emote_as_one_composite():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage("FOCUS PETPET", emotes, [])

    assert spans == [
        {
            "kind": "composite",
            "tokens": ["FOCUS", "PETPET"],
            "count": 1,
            "metadata": {
                "composition": "postfixModifier",
                "modifier": "PETPET",
                "modifierKind": "postfixEmote",
                "baseEmote": "FOCUS",
            },
        }
    ]
    evaluated = chatSemantics._evaluate(None, {"spans": spans})
    assert evaluated["lexicallyComplete"] is True
    assert evaluated["semanticallyComplete"] is False


def test_any_configured_postfix_modifier_uses_same_binding_rule():
    emotes = _emotes("FOCUS", modifiers=("PETPET", "HEADPAT"))

    petpet = chatSemantics._lexMessage("FOCUS PETPET", emotes, [])
    headpat = chatSemantics._lexMessage("FOCUS HEADPAT", emotes, [])

    assert petpet[0]["tokens"] == ["FOCUS", "PETPET"]
    assert headpat[0]["tokens"] == ["FOCUS", "HEADPAT"]
    assert headpat[0]["metadata"]["modifier"] == "HEADPAT"


def test_modifier_binding_is_case_insensitive_and_preserves_canonical_names():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage("focus petpet", emotes, [])

    assert spans[0]["tokens"] == ["FOCUS", "PETPET"]
    assert spans[0]["metadata"]["baseEmote"] == "FOCUS"
    assert spans[0]["metadata"]["modifier"] == "PETPET"


def test_repeated_modifier_composites_collapse_as_the_same_reaction_unit():
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


def test_modifier_composite_accepts_explicit_multiplier_after_modifier():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage("FOCUS PETPET x3", emotes, [])

    assert spans[0]["tokens"] == ["FOCUS", "PETPET"]
    assert spans[0]["count"] == 3


def test_unbound_modifier_stays_normal_text_and_reports_missing_base_warning():
    emotes = _emotes("FOCUS")

    spans = chatSemantics._lexMessage("PETPET", emotes, [])
    warnings = chatSemantics._modifierWarnings("PETPET", emotes)

    assert spans == [{"kind": "text", "text": "PETPET"}]
    assert chatSemantics._evaluate(None, {"spans": spans})["lexicallyComplete"] is False
    assert warnings == [
        {
            "code": "unboundModifier",
            "modifier": "PETPET",
            "modifierKind": "postfixEmote",
            "sourceToken": "PETPET",
            "tokenIndex": 0,
            "precedingToken": None,
            "message": (
                "Modifier 'PETPET' is not immediately preceded by a recognized emote; "
                "the preceding base may be missing from chatEmotes.json, or a modifier chain was used."
            ),
        }
    ]


def test_modifier_after_unknown_base_stays_normal_message_and_warns_about_preceding_token():
    emotes = _emotes("FOCUS")
    message = "missingEmote PETPET"

    spans = chatSemantics._lexMessage(message, emotes, [])
    warnings = chatSemantics._modifierWarnings(message, emotes)

    assert spans == [{"kind": "text", "text": message}]
    assert warnings[0]["code"] == "unboundModifier"
    assert warnings[0]["precedingToken"] == "missingEmote"


def test_modifier_followed_by_modifier_is_invalid_and_prevents_reaction_only_classification():
    emotes = _emotes("FOCUS", modifiers=("PETPET", "HEADPAT"))
    message = "FOCUS PETPET HEADPAT"

    spans = chatSemantics._lexMessage(message, emotes, [])
    warnings = chatSemantics._modifierWarnings(message, emotes)

    assert spans[0]["kind"] == "composite"
    assert spans[0]["tokens"] == ["FOCUS", "PETPET"]
    assert spans[1] == {"kind": "text", "text": "HEADPAT"}
    assert len(warnings) == 1
    assert warnings[0]["modifier"] == "HEADPAT"
    assert warnings[0]["precedingToken"] == "PETPET"
    assert chatSemantics._evaluate(None, {"spans": spans})["lexicallyComplete"] is False


def test_two_modifiers_without_base_are_both_invalid():
    emotes = _emotes("FOCUS", modifiers=("PETPET", "HEADPAT"))
    message = "PETPET HEADPAT"

    spans = chatSemantics._lexMessage(message, emotes, [])
    warnings = chatSemantics._modifierWarnings(message, emotes)

    assert spans == [{"kind": "text", "text": message}]
    assert [warning["modifier"] for warning in warnings] == ["PETPET", "HEADPAT"]
    assert warnings[1]["precedingToken"] == "PETPET"


def test_postfix_binding_is_tighter_than_fixed_composite_matching():
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
    assert chatSemantics._modifierWarnings(
        "ReallyGunPull Tutel PETPET",
        emotes,
    ) == []


def test_line_semantic_persists_unbound_modifier_warning_while_retaining_normal_message():
    emotes = _emotes("FOCUS")

    semantic = chatSemantics._lineSemantic(
        "viewer: missingEmote PETPET",
        emotes=emotes,
        composites=[],
    )

    assert semantic["kind"] == "userMessage"
    assert semantic["body"] == "missingEmote PETPET"
    assert semantic["spans"] == [{"kind": "text", "text": "missingEmote PETPET"}]
    assert semantic["warnings"][0]["code"] == "unboundModifier"
    assert semantic["warnings"][0]["modifier"] == "PETPET"
    assert semantic["warnings"][0]["precedingToken"] == "missingEmote"
