from __future__ import annotations

from collections.abc import Mapping

_SEMANTIC_KEYS = ("semanticClass", "entity", "target")
_TRUSTED_CLASSIFICATION_SOURCE = "userDefined"


def _meaning(metadata: Mapping[str, object]) -> dict[str, object] | None:
    if metadata.get("classificationSource") != _TRUSTED_CLASSIFICATION_SOURCE:
        return None
    meaning = {key: metadata[key] for key in _SEMANTIC_KEYS if key in metadata}
    if not meaning:
        return None
    meaning["classificationSource"] = _TRUSTED_CLASSIFICATION_SOURCE
    return meaning


def _evaluateSpan(span: dict[str, object], *, multiplier: int = 1) -> tuple[bool, bool, list[dict[str, object]]]:
    kind = span.get("kind")

    if kind in {"emote", "composite"}:
        metadata = span.get("metadata")
        if not isinstance(metadata, Mapping):
            raise TypeError(f"Chat semantic span {kind!r} requires metadata.")
        count = span.get("count", 1)
        if type(count) is not int or count <= 0:
            raise ValueError(f"Chat semantic span {kind!r} requires a positive exact integer count.")
        meaning = _meaning(metadata)
        if meaning is None:
            return True, False, []
        return True, True, [{"meaning": meaning, "count": count * multiplier}]

    if kind == "repeat":
        count = span.get("count")
        nested = span.get("spans")
        if type(count) is not int or count <= 1:
            raise ValueError("Chat semantic repeat spans require an exact integer count greater than one.")
        if not isinstance(nested, list) or not nested:
            raise ValueError("Chat semantic repeat spans require a non-empty spans list.")
        lexical = True
        semantic = True
        units: list[dict[str, object]] = []
        for nestedSpan in nested:
            if not isinstance(nestedSpan, dict):
                raise TypeError("Chat semantic repeat spans must contain objects.")
            nestedLexical, nestedSemantic, nestedUnits = _evaluateSpan(
                nestedSpan,
                multiplier=multiplier * count,
            )
            lexical = lexical and nestedLexical
            semantic = semantic and nestedSemantic
            units.extend(nestedUnits)
        return lexical, semantic, units

    if kind in {"text", "command"}:
        return False, False, []

    raise ValueError(f"Unsupported chat semantic span kind: {kind!r}.")


def _evaluate(_ctx, payload):
    if not isinstance(payload, dict):
        raise ValueError("Chat semantic evaluation requires an object payload.")
    spans = payload.get("spans")
    if not isinstance(spans, list):
        raise TypeError("Chat semantic evaluation spans must be a list.")
    if not spans:
        return {
            "lexicallyComplete": False,
            "semanticallyComplete": False,
            "aggregationEligible": False,
            "semanticUnits": [],
            "structurallyCompressed": False,
        }

    lexical = True
    semantic = True
    units: list[dict[str, object]] = []
    structurallyCompressed = False
    for span in spans:
        if not isinstance(span, dict):
            raise TypeError("Chat semantic evaluation spans must contain objects.")
        if span.get("kind") == "repeat":
            structurallyCompressed = True
        spanLexical, spanSemantic, spanUnits = _evaluateSpan(span)
        lexical = lexical and spanLexical
        semantic = semantic and spanSemantic
        units.extend(spanUnits)

    return {
        "lexicallyComplete": lexical,
        "semanticallyComplete": semantic,
        "aggregationEligible": semantic,
        "semanticUnits": units if semantic else [],
        "structurallyCompressed": structurallyCompressed,
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.chatSemantics@1", _evaluate)
