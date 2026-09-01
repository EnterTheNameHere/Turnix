from __future__ import annotations

import re

_USERNAME_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _definedUsers(config: dict[str, object]) -> dict[str, str]:
    definition = config.get("definedUsers", {})
    if not isinstance(definition, dict):
        raise TypeError("Application config definedUsers must be an object.")

    normalized: dict[str, str] = {}
    originalNames: dict[str, str] = {}
    for username, value in definition.items():
        if type(username) is not str or not username:
            raise ValueError("definedUsers keys must be non-empty usernames.")
        if _USERNAME_TOKEN_RE.fullmatch(username) is None:
            raise ValueError(f"definedUsers key {username!r} must use username characters [A-Za-z0-9_].")
        if not isinstance(value, dict) or type(value.get("identity")) is not str or not value["identity"]:
            raise ValueError(f"definedUsers[{username!r}] must define a non-empty identity string.")
        folded = username.casefold()
        if folded in normalized:
            raise ValueError(
                "definedUsers must not contain usernames that differ only by case: "
                f"{originalNames[folded]!r} and {username!r}."
            )
        normalized[folded] = value["identity"]
        originalNames[folded] = username
    return normalized


def _authorList(payload: dict[str, object]) -> list[str]:
    authors = payload.get("authors", [])
    if not isinstance(authors, list) or any(type(author) is not str or not author for author in authors):
        raise TypeError("Identity resolution authors must be a list of non-empty strings.")
    if any(_USERNAME_TOKEN_RE.fullmatch(author) is None for author in authors):
        raise ValueError("Identity resolution authors must use username characters [A-Za-z0-9_].")
    return authors


def _textList(payload: dict[str, object]) -> list[str]:
    texts = payload.get("texts", [])
    if not isinstance(texts, list) or any(type(text) is not str for text in texts):
        raise TypeError("Identity resolution texts must be a list of strings.")
    return texts


def _identityMap(authors: list[str], definedUsers: dict[str, str]) -> dict[str, str]:
    identities: dict[str, str] = {}
    nextAnonymous = 1
    for author in authors:
        folded = author.casefold()
        if folded in identities:
            continue
        preserved = definedUsers.get(folded)
        if preserved is not None:
            identities[folded] = preserved
            continue
        identities[folded] = f"anonymized_{nextAnonymous}"
        nextAnonymous += 1
    return identities


def _replacementMap(
    authors: list[str],
    identities: dict[str, str],
    definedUsers: dict[str, str],
) -> dict[str, str]:
    replacements = dict(definedUsers)
    for author in authors:
        folded = author.casefold()
        replacements[folded] = identities[folded]
    return replacements


def _replaceKnownIdentities(text: str, replacements: dict[str, str]) -> str:
    """Replaces complete username tokens in one linear scan of text."""

    if not replacements or not text:
        return text

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return replacements.get(token.casefold(), token)

    return _USERNAME_TOKEN_RE.sub(replace, text)


def _assertNoRawAnonymousIdentity(texts: list[str], authors: list[str], definedUsers: dict[str, str]) -> None:
    anonymous = {author.casefold() for author in authors if author.casefold() not in definedUsers}
    if not anonymous:
        return
    for text in texts:
        for match in _USERNAME_TOKEN_RE.finditer(text):
            if match.group(0).casefold() in anonymous:
                raise RuntimeError(
                    "Identity sanitization left a non-preserved raw username in rendered text: "
                    f"{match.group(0)!r}."
                )


def _resolveAndSanitize(ctx, payload):
    if not isinstance(payload, dict):
        raise ValueError("Identity resolution requires an object payload.")

    authors = _authorList(payload)
    texts = _textList(payload)
    definedUsers = _definedUsers(ctx.config)
    identities = _identityMap(authors, definedUsers)
    replacements = _replacementMap(authors, identities, definedUsers)

    displayAuthors = [identities[author.casefold()] for author in authors]
    sanitizedTexts = [_replaceKnownIdentities(text, replacements) for text in texts]
    _assertNoRawAnonymousIdentity(sanitizedTexts, authors, definedUsers)

    return {
        "displayAuthors": displayAuthors,
        "texts": sanitizedTexts,
        "anonymousIdentityCount": len({identity for identity in identities.values() if identity.startswith("anonymized_")}),
        "preservedIdentityCount": len({author.casefold() for author in authors if author.casefold() in definedUsers}),
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.identity@1", _resolveAndSanitize)
