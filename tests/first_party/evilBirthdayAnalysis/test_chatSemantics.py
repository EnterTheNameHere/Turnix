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


def _evaluate(spans):
    return chatSemantics._evaluate(None, {"spans": spans})


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

    assert result == {
        "lexicallyComplete": True,
        "semanticallyComplete": True,
        "aggregationEligible": True,
        "semanticUnits": [
            {
                "meaning": {"semanticClass": "praise", "classificationSource": "userDefined"},
                "count": 3,
            }
        ],
        "structurallyCompressed": False,
    }


def test_recognized_but_unclassified_emote_is_lexical_not_semantic():
    result = _evaluate([{"kind": "emote", "name": "Clap", "count": 2, "metadata": {}}])

    assert result["lexicallyComplete"] is True
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False
    assert result["semanticUnits"] == []


def test_known_semantic_emote_with_residual_text_is_not_flattenable():
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
    assert result["semanticUnits"] == []


def test_confirmed_composite_keeps_target_as_part_of_semantic_identity():
    result = _evaluate(
        [
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
    )

    assert result["semanticallyComplete"] is True
    assert result["semanticUnits"] == [
        {
            "meaning": {
                "semanticClass": "negative",
                "target": "Vedal",
                "classificationSource": "userDefined",
            },
            "count": 2,
        }
    ]


def test_exact_repeat_of_semantic_units_multiplies_occurrence_count_without_creating_users():
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
    assert result["semanticallyComplete"] is True
    assert result["semanticUnits"][0]["count"] == 5


def test_exact_repeat_remains_safe_structural_compression_even_when_meaning_is_incomplete():
    result = _evaluate(
        [
            {
                "kind": "repeat",
                "count": 4,
                "spans": [
                    {
                        "kind": "composite",
                        "tokens": ["ReallyGunPull", "Tutel"],
                        "count": 1,
                        "metadata": {
                            "semanticClass": "negative",
                            "target": "Vedal",
                            "classificationSource": "userDefined",
                        },
                    },
                    {"kind": "text", "text": "COME TO HER PARTY"},
                ],
            }
        ]
    )

    assert result["structurallyCompressed"] is True
    assert result["lexicallyComplete"] is False
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False
    assert result["semanticUnits"] == []


def test_commands_are_preserved_but_not_treated_as_preexisting_semantic_meaning():
    result = _evaluate([{"kind": "command", "command": "abandonedarchive", "arguments": []}])

    assert result["lexicallyComplete"] is False
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False


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
