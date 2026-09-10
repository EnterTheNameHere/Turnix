# file: first-party/applications/evilBirthdayAnalysis/packs/chatSemantics/entry.py ; version: 1
from __future__ import annotations

import importlib.util
from pathlib import Path


_IMPLEMENTATION_PATH = Path(__file__).with_name("codeEntry.py")
_SPEC = importlib.util.spec_from_file_location(
    "evilBirthdayAnalysisChatSemanticsImplementation",
    _IMPLEMENTATION_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Cannot load chat semantics implementation from {_IMPLEMENTATION_PATH}.")
_impl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_impl)

_POSTFIX_EMOTE_KIND = "postfixEmote"
_RESERVED_MODIFIER_METADATA_KEYS = {
    "composition",
    "modifier",
    "modifierKind",
    "baseEmote",
}


class _EmoteVocabulary(dict[str, dict[str, object]]):
    def __init__(
        self,
        *args,
        postfixModifiers: dict[str, dict[str, object]],
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.postfixModifiers = postfixModifiers


def _modifierDefinitions(
    emotes: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    modifiers = getattr(emotes, "postfixModifiers", None)
    return modifiers if isinstance(modifiers, dict) else {}


def _vocabulary(ctx):
    """Loads emotes, fixed composites, and configured lexical modifiers."""
    path = ctx.config.get("chatEmotesFile", "chatEmotes.json")
    if type(path) is not str:
        raise ValueError("Application config chatEmotesFile must be a string path.")

    observed = ctx.io.readObservedJson(path)
    definition = observed.get("value")
    after = observed.get("observation")
    if not isinstance(definition, dict) or not isinstance(after, dict):
        raise RuntimeError("Observed chat vocabulary read returned invalid data.")

    emotes = definition.get("emotes")
    composites = definition.get("composites", [])
    modifiers = definition.get("modifiers", {})
    if (
        not isinstance(emotes, dict)
        or not isinstance(composites, list)
        or not isinstance(modifiers, dict)
    ):
        raise ValueError(
            "Chat emote vocabulary requires emotes object and optional composites list/modifiers object."
        )

    normalizedModifiers: dict[str, dict[str, object]] = {}
    canonicalModifierNames: dict[str, str] = {}
    for name, definitionValue in modifiers.items():
        if type(name) is not str or not name or not isinstance(definitionValue, dict):
            raise ValueError(
                "Chat modifier definitions require non-empty string names and object metadata."
            )
        folded = name.casefold()
        if folded in canonicalModifierNames:
            raise ValueError(
                "Chat modifier definitions must be unique case-insensitively; "
                f"{canonicalModifierNames[folded]!r} conflicts with {name!r}."
            )
        kind = definitionValue.get("kind")
        if kind != _POSTFIX_EMOTE_KIND:
            raise ValueError(
                f"Chat modifier {name!r} has unsupported kind {kind!r}; "
                f"currently supported: {_POSTFIX_EMOTE_KIND!r}."
            )
        conflicting = _RESERVED_MODIFIER_METADATA_KEYS.intersection(definitionValue)
        conflicting.discard("kind")
        if conflicting:
            raise ValueError(
                f"Chat modifier {name!r} uses reserved metadata key(s): "
                + ", ".join(sorted(conflicting))
                + "."
            )
        canonicalModifierNames[folded] = name
        normalizedModifiers[folded] = {
            "name": name,
            "kind": kind,
            "metadata": {
                key: value
                for key, value in definitionValue.items()
                if key != "kind"
            },
        }

    normalizedEmoteValues: dict[str, dict[str, object]] = {}
    canonicalEmoteNames: dict[str, str] = {}
    for name, metadata in emotes.items():
        if type(name) is not str or not name or not isinstance(metadata, dict):
            raise ValueError(
                "Chat emote definitions require non-empty string names and object metadata."
            )
        folded = name.casefold()
        if folded in canonicalModifierNames:
            raise ValueError(
                f"Chat token {name!r} cannot be both an emote and a modifier."
            )
        if folded in canonicalEmoteNames:
            raise ValueError(
                "Chat emote definitions must be unique case-insensitively; "
                f"{canonicalEmoteNames[folded]!r} conflicts with {name!r}."
            )
        canonicalEmoteNames[folded] = name
        normalizedEmoteValues[folded] = {
            "name": name,
            "metadata": dict(metadata),
        }

    normalizedEmotes = _EmoteVocabulary(
        normalizedEmoteValues,
        postfixModifiers=normalizedModifiers,
    )

    normalizedComposites: list[dict[str, object]] = []
    seenPatterns: set[tuple[str, ...]] = set()
    for compositeDefinition in composites:
        if not isinstance(compositeDefinition, dict):
            raise ValueError("Chat composite definitions must be objects.")
        tokens = compositeDefinition.get("tokens")
        if (
            not isinstance(tokens, list)
            or len(tokens) < 2
            or any(type(token) is not str or not token for token in tokens)
        ):
            raise ValueError(
                "Chat composite definitions require at least two non-empty string tokens."
            )
        pattern = tuple(tokens)
        foldedPattern = tuple(token.casefold() for token in pattern)
        unknown = [
            token
            for token in pattern
            if token.casefold() not in normalizedEmotes
        ]
        if unknown:
            raise ValueError(
                f"Chat composite references unknown emote token(s): {', '.join(unknown)}."
            )
        if foldedPattern in seenPatterns:
            raise ValueError(f"Duplicate chat composite pattern: {' '.join(pattern)}.")
        seenPatterns.add(foldedPattern)
        metadata = {
            key: value
            for key, value in compositeDefinition.items()
            if key != "tokens"
        }
        normalizedComposites.append(
            {
                "tokens": tuple(
                    canonicalEmoteNames[token.casefold()]
                    for token in pattern
                ),
                "foldedTokens": foldedPattern,
                "metadata": metadata,
            }
        )

    normalizedComposites.sort(key=lambda item: len(item["tokens"]), reverse=True)
    return normalizedEmotes, normalizedComposites, after


def _modifierWarnings(
    message: str,
    emotes: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    modifiers = _modifierDefinitions(emotes)
    if not modifiers:
        return []

    tokens = message.split()
    warnings: list[dict[str, object]] = []
    for index, token in enumerate(tokens):
        modifier = modifiers.get(token.casefold())
        if modifier is None:
            continue

        precedingToken = tokens[index - 1] if index > 0 else None
        if (
            precedingToken is not None
            and precedingToken.casefold() in emotes
        ):
            continue

        canonicalName = str(modifier["name"])
        warnings.append(
            {
                "code": "unboundModifier",
                "modifier": canonicalName,
                "modifierKind": modifier["kind"],
                "sourceToken": token,
                "tokenIndex": index,
                "precedingToken": precedingToken,
                "message": (
                    f"Modifier {canonicalName!r} is not immediately preceded by a recognized emote; "
                    "the preceding base may be missing from chatEmotes.json, or a modifier chain was used."
                ),
            }
        )
    return warnings


def _matchComposite(
    tokens: list[str],
    index: int,
    composites: list[dict[str, object]],
    modifiers: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    for composite in composites:
        pattern = composite["foldedTokens"]
        nextIndex = index + len(pattern)
        candidate = tuple(
            token.casefold()
            for token in tokens[index:nextIndex]
        )
        if candidate != pattern:
            continue

        # A postfix modifier binds to the immediately preceding plain emote.
        # Do not let a fixed composite greedily consume that base emote.
        if (
            nextIndex < len(tokens)
            and tokens[nextIndex].casefold() in modifiers
        ):
            continue
        return composite
    return None


def _lexMessage(
    message: str,
    emotes: dict[str, dict[str, object]],
    composites: list[dict[str, object]],
) -> list[dict[str, object]]:
    modifiers = _modifierDefinitions(emotes)
    tokens = message.split()
    spans: list[dict[str, object]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        emote = emotes.get(token.casefold())

        if emote is not None and index + 1 < len(tokens):
            modifier = modifiers.get(tokens[index + 1].casefold())
            if modifier is not None:
                modifierMetadata = dict(modifier["metadata"])
                count, nextIndex = _impl._occurrenceCount(tokens, index + 2)
                _impl._appendSpan(
                    spans,
                    {
                        "kind": "composite",
                        "tokens": [emote["name"], modifier["name"]],
                        "count": count,
                        "metadata": {
                            **modifierMetadata,
                            "composition": "postfixModifier",
                            "modifier": modifier["name"],
                            "modifierKind": modifier["kind"],
                            "baseEmote": emote["name"],
                        },
                    },
                )
                index = nextIndex
                continue

        composite = _matchComposite(tokens, index, composites, modifiers)
        if composite is not None:
            pattern = composite["tokens"]
            count, nextIndex = _impl._occurrenceCount(
                tokens,
                index + len(pattern),
            )
            _impl._appendSpan(
                spans,
                {
                    "kind": "composite",
                    "tokens": list(pattern),
                    "count": count,
                    "metadata": dict(composite["metadata"]),
                },
            )
            index = nextIndex
            continue

        if emote is not None:
            count, nextIndex = _impl._occurrenceCount(tokens, index + 1)
            _impl._appendSpan(
                spans,
                {
                    "kind": "emote",
                    "name": emote["name"],
                    "count": count,
                    "metadata": dict(emote["metadata"]),
                },
            )
            index = nextIndex
            continue

        if token.startswith("!") and len(token) > 1 and not spans:
            _impl._appendSpan(
                spans,
                {"kind": "command", "command": token[1:], "arguments": []},
            )
            index += 1
            continue

        if spans and spans[-1].get("kind") == "command":
            arguments = spans[-1]["arguments"]
            if isinstance(arguments, list):
                arguments.append(token)
                index += 1
                continue

        _impl._appendSpan(spans, {"kind": "text", "text": token})
        index += 1

    spans = _impl._collapseWholeSequence(spans)
    return _impl._collapseRepeatedTextSequence(message, spans)


# Keep the large semantic materialization implementation in codeEntry.py while
# this entry layer owns vocabulary-driven lexical extension. Its replacements
# are installed into that module's global namespace before capabilities are
# registered, so all persistent derivation paths use the configured grammar.
_impl._vocabulary = _vocabulary
_impl._petpetWarnings = _modifierWarnings
_impl._matchComposite = _matchComposite
_impl._lexMessage = _lexMessage


# Expose helpers for focused tests and debugging.
_evaluate = _impl._evaluate
_lineSemantic = _impl._lineSemantic
_renderSpans = _impl._renderSpans


def onLoad(ctx):
    _impl.onLoad(ctx)
