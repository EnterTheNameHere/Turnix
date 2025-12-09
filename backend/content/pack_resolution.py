# backend/content/pack_resolution.py
from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.content.pack_descriptor import (
    PackKind,
    PackRequest,
    PackDescriptor,
    PackDescriptorRegistry,
)
from backend.semver.semver import SemVerPackRequirement, parseSemVerPackRequirement

logger = logging.getLogger(__name__)

__all__ = [
    "PackResolutionError",
    "PackNotFoundError",
    "PackAmbiguousError",
    "parsePackRefString",
    "resolvePackSelector",
    "tryResolvePackSelector",
]



# ------------------------------------------------------------------ #
# Errors
# ------------------------------------------------------------------ #

class PackResolutionError(RuntimeError):
    """Base class for pack resolution errors."""
    
    def __init__(
        self,
        message: str,
        *,
        request: PackRequest | None = None
    ) -> None:
        super().__init__(message)
        self.request: PackRequest | None = request



class PackNotFoundError(PackResolutionError):
    """Raised when no pack matches the selector/constraints."""



class PackAmbiguousError(PackResolutionError):
    """Raised when multiple candidates exist and selector is not specific enough."""



# ------------------------------------------------------------------ #
# PackRefString parsing
# ------------------------------------------------------------------ #

@dataclass(slots=True)
class ParsedPackRef:
    """Result of parsing a PackRefString."""
    author: str | None
    packTreeId: str
    semverRequirement: SemVerPackRequirement | None



def _stripOrNone(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None



def parsePackRefString(
    ref: str,
    *,
    kind: PackKind | None = None,
) -> PackRequest:
    """
    Parse a textual selector into a PackRequest.
    
    Supported forms (no schema):
    
        [<author>"@"]<packTreeId>["@"<SemVerPackRequirement>]
    
    Examples:
        "avatars"                     # no author, id, no version
        "Anthony@avatars"             #    author, id, no version
        "Turnix@ui.trace-list@2.5.3"  #    author, id,    version
        "Turnix@main-menu.ui@^3"      #    author, id,    version
        "main-menu@2.3.4"             # no author, id,    version
    
    Notes:
      - This parser deliberately rejects strings containing "://".
        Fully qualified URIs (mod://..., appPack://) must be handled
        by the ResourceUriResolver.
    """
    if not ref:
        raise ValueError("Empty PackRefString is not allowed")
    
    text = ref.strip()
    if not text:
        raise ValueError("PackRefString cannot be just whitespace")
    
    if "://" in text:
        raise ValueError(
            f"PackRefString {text!r} looks like a full URI. "
            "Use ResourceUriResolver for scheme-based URIs."
        )
    
    author: str | None = None
    packTreeId: str
    requirement: SemVerPackRequirement | None = None
    
    # Primary grammar with "@"
    if "@" in text:
        parts = text.split("@")
        if len(parts) == 2:
            # author@packTreeId OR packTreeId@semverRange
            firstPart, secondPart = parts
            try:
                requirement = parseSemVerPackRequirement(secondPart.strip())
            except Exception as err:
                logger.debug(
                    "parsePackRefString: Invalid SemVer version format in '%s': %s",
                    secondPart,
                    err,
                )
                requirement = None
            
            if requirement is not None:
                # packTreeId@semverRange
                authorPart = None
                packTreeId = _stripOrNone(firstPart) or ""
            else:
                # author@packTreeId
                author = _stripOrNone(firstPart)
                packTreeId = _stripOrNone(secondPart) or ""
            
            if not packTreeId:
                raise ValueError(f"PackRefString {text!r} has empty packTreeId part")
        elif len(parts) == 3:
            # author@packTreeId@semverRange
            authorPart, idPart, reqPart = parts
            author = _stripOrNone(authorPart)
            packTreeId = _stripOrNone(idPart) or ""
            if not packTreeId:
                raise ValueError(f"PackRefString {text!r} has empty packTreeId part")
            reqPart = _stripOrNone(reqPart)
            requirement = parseSemVerPackRequirement(reqPart) 
        else:
            raise ValueError(
                f"PackRefString {text!r} contains too many '@' segments "
                "(expected at most author@id@range)"
            )
    else:
        # No author, id, no version
        packTreeId = text
    
    if not packTreeId:
        raise ValueError(f"Invalid PackRefString {text!r}: missing packTreeId")
    
    return PackRequest(
        author=author,
        packTreeId=packTreeId,
        semverRequirement=requirement,
        kind=kind,
    )



# ------------------------------------------------------------------ #
# Resolution helpers
# ------------------------------------------------------------------ #

def _pickAuthorForLookup(
    request: PackRequest,
    requestingPack: PackDescriptor | None,
) -> str | None:
    """
    Determine which author to use for lookup.
    
    Rules:
      - If request.author is set, use it.
      - Else if requestingPack is provided, inherit requestingPack.effectiveAuthor
      - Else, return None (wildcard).
    """
    if request.author is not None:
        return request.author
    if requestingPack is not None:
        return requestingPack.effectiveAuthor
    return None



def _ensureNoAuthorAmbiguity(
    registry: PackDescriptorRegistry,
    *,
    packTreeId: str,
    kind: PackKind | None,
    author: str | None,
    request: PackRequest,
) -> None:
    """
    If author is None, check whether multiple authors exist for the same
    (packTreeId, kind). If yes, raise PackAmbiguousError.
    
    This keeps behaviour predictable and matches the spec requirement that
    selectors without authors must not resolve to different packs in a
    configuration-dependent way.
    """
    if author is not None:
        return
    
    candidates = registry.findCandidates(
        packTreeId=packTreeId,
        kind=kind,
        author=None,
    )
    if not candidates:
        return
    
    authors = {desc.effectiveAuthor for desc in candidates}
    if len(authors) > 1:
        msg = (
            f"Ambiguous pack selector for packTreeId={packTreeId!r}: "
            f"multiple authors discovered {sorted(authors)!r}, but no author "
            "was specified in the selector"
        )
        raise PackAmbiguousError(msg, request=request)



def resolvePackSelector(
    registry: PackDescriptorRegistry,
    selector: str | PackRequest,
    *,
    kind: PackKind | None = None,
    requestingPack: PackDescriptor | None = None,
    requirementOverride: SemVerPackRequirement | None = None,
    preferSaves: bool = True,
) -> PackDescriptor:
    """
    Resolve a PackRefString or PackRequest to a concrete PackDescriptor.
    
    Parameters:
        registry:
            PackDescriptorRegistry built from discovery.
        selector:
            Either a PackRefString (text) or a pre-parsed PackRequest.
        kind:
            Optional pack kind filter (appPack, viewPack, mod, contentPack, savePack).
            If selector is a PackRequest with kind already set, that wins.
        requestingPack:
            Optional PackDescriptor of the pack that is making the request.
            Used to inherit author when the selector omits it.
        requirementOverride:
            Optional SemVer requirement that overrides the selector's requirement.
        preferSaves:
            If True, save-layer packs win ties over content-layer packs.
    
    Behaviour:
      - Parses selector (if string) into PackRequest.
      - Decides which author to use for lookup (see _pickAuthorForLookup)
      - If author is still None and multiple authors exist, raises
        PackAmbiguousError.
      - Uses registry.resolveBest(...) to apply SemVer resolution.
      - Raises PackNotFoundError if no matching pack is found.
    
    Raises:
        PackNotFoundError
        PackAmbiguousError
        ValueError (for malformed selectors)
    """
    if isinstance(selector, PackRequest):
        request = selector
        if kind is not None and request.kind is None:
            request.kind = kind
    else:
        request = parsePackRefString(selector, kind=kind)
    
    lookupKind = request.kind or kind
    author = _pickAuthorForLookup(request, requestingPack)
    requirement: SemVerPackRequirement | None = (
        requirementOverride
        if requirementOverride is not None
        else request.semverRequirement
    )
    
    _ensureNoAuthorAmbiguity(
        registry,
        packTreeId=request.packTreeId,
        kind=lookupKind,
        author=author,
        request=request,
    )
    
    desc = registry.resolveBest(
        packTreeId=request.packTreeId,
        kind=lookupKind,
        author=author,
        requirement=requirement,
        preferSaves=preferSaves,
    )
    if desc is None:
        msg = (
            "No pack matched selector: "
            f"packTreeId={request.packTreeId!r}, "
            f"kind={lookupKind}, author={author!r}, "
            f"requirement={requirement!r}"
        )
        raise PackNotFoundError(msg, request=request)
    
    return desc



def tryResolvePackSelector(
    registry: PackDescriptorRegistry,
    selector: str | PackRequest,
    *,
    kind: PackKind,
    requestingPack: PackDescriptor | None = None,
    requirementOverride: SemVerPackRequirement | None = None,
    preferSaves: bool = True,
) -> PackDescriptor | None:
    """
    Best-effort wrapper around resolvePackSelector.
    
    Returns:
      - PackDescriptor on success
      - None if nothing can be resolved (PackNotFoundError)
    
    Any other PackResolutionError (for example, PackAmbiguousError) is propagated
    so the caller is forced to handle real ambiguity explicitly.
    """
    try:
        return resolvePackSelector(
            registry,
            selector,
            kind=kind,
            requestingPack=requestingPack,
            requirementOverride=requirementOverride,
            preferSaves=preferSaves,
        )
    except PackNotFoundError as err:
        logger.debug("tryResolvePackSelector: not found: %s", err)
    
    return None
