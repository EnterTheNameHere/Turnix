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
    / "identity"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayIdentityCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
identity = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(identity)


class _Ctx:
    def __init__(self, definedUsers=None):
        self.config = {"definedUsers": {} if definedUsers is None else definedUsers}


def test_resolver_preserves_defined_identity_and_anonymizes_other_authors_per_call():
    result = identity._resolveAndSanitize(
        _Ctx({"vedal987": {"identity": "Vedal"}}),
        {
            "authors": ["viewerA", "vedal987", "viewerA", "viewerB"],
            "texts": ["hello", "hello", "again", "hello"],
        },
    )

    assert result["displayAuthors"] == ["anonymized_1", "Vedal", "anonymized_1", "anonymized_2"]
    assert result["anonymousIdentityCount"] == 2
    assert result["preservedIdentityCount"] == 1


def test_resolver_scope_restarts_for_each_inference_call():
    ctx = _Ctx()

    first = identity._resolveAndSanitize(ctx, {"authors": ["alice", "bob"], "texts": []})
    second = identity._resolveAndSanitize(ctx, {"authors": ["bob", "alice"], "texts": []})

    assert first["displayAuthors"] == ["anonymized_1", "anonymized_2"]
    assert second["displayAuthors"] == ["anonymized_1", "anonymized_2"]


def test_resolver_rewrites_known_identity_mentions_in_text_case_insensitively():
    result = identity._resolveAndSanitize(
        _Ctx({"vedal987": {"identity": "Vedal"}}),
        {
            "authors": ["viewerA", "vedal987"],
            "texts": [
                "@viewerA what did VEDAL987 mean?",
                "viewerA replied to vedal987",
            ],
        },
    )

    assert result["texts"] == [
        "@anonymized_1 what did Vedal mean?",
        "anonymized_1 replied to Vedal",
    ]
    assert "viewerA" not in "\n".join(result["texts"])
    assert "vedal987" not in "\n".join(result["texts"]).casefold()


def test_resolver_sanitizes_defined_identity_even_when_user_is_not_an_author():
    result = identity._resolveAndSanitize(
        _Ctx({"vedal987": {"identity": "Vedal"}}),
        {
            "authors": ["viewerA"],
            "texts": ["I think vedal987 caused this"],
        },
    )

    assert result["texts"] == ["I think Vedal caused this"]


def test_resolver_does_not_replace_username_inside_larger_username_token():
    result = identity._resolveAndSanitize(
        _Ctx(),
        {
            "authors": ["cat"],
            "texts": ["cat category @cat cat_2 bobcat"],
        },
    )

    assert result["texts"] == ["anonymized_1 category @anonymized_1 cat_2 bobcat"]


def test_definedUsers_rejects_casefold_duplicate_accounts():
    with pytest.raises(ValueError, match="differ only by case"):
        identity._definedUsers(
            {
                "definedUsers": {
                    "Vedal987": {"identity": "Vedal"},
                    "vedal987": {"identity": "Vedal"},
                }
            }
        )


def test_definedUsers_requires_explicit_non_empty_identity():
    with pytest.raises(ValueError, match="non-empty identity"):
        identity._definedUsers({"definedUsers": {"vedal987": {}}})
