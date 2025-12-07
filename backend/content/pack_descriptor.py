# backend/content/pack_descriptor.py
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import json5

from backend.app.globals import configBool, getTracer, getContentRootsService
from backend.semver.semver import (
    SemVerPackVersion,
    SemVerPackRequirement,
    SemVerResolver,
    parseSemVerPackVersion,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AssetMeta",
    "PackKind",
    "LayerKind",
    "VisibilityKind",
    "PackRequest",
    "PackDescriptor",
    "PackDescriptorRegistry",
    "buildPackDescriptorRegistry",
]



_MANIFEST_NAMES: tuple[str, ...] = ("manifest.json5", "manifest.json")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")



class PackKind(Enum):
    APP = "appPack"
    VIEW = "viewPack"
    MOD = "mod"
    CONTENT = "contentPack"
    SAVE = "savePack"



class LayerKind(Enum):
    FIRST_PARTY = "first-party"
    THIRD_PARTY = "third-party"
    CUSTOM = "custom"
    SAVES = "saves"



class VisibilityKind(Enum):
    PUBLIC = "public"
    PRIVATE = "private"



@dataclass(slots=True)
class AssetMeta:
    # Logical name used in lookups (e.g. "Sandy.png" or "config.json")
    logicalName: str
    # Path relative to the pack root (e.g. Path("assets/Sandy.png"))
    relPath: Path
    # High-level asset kind: "image", "config", "text", "binary", ...
    kind: str



@dataclass(slots=True)
class PackRequest:
    # From "<author>@" prefix in PackRefString, or None if omitted
    author: str | None
    # Hierarchical id ("ui.trace.trace-view")
    packTreeId: str
    # Semantic version requirement (parsed), or None if no constraint
    semverRequirement: SemVerPackRequirement | None
    # Optional filter by pack kind (mod, appPack, ...)
    kind: PackKind | None = None



@dataclass(frozen=True, slots=True, kw_only=True)
class PackDescriptor:
    """
    Canonical, immutable description of a single discovered pack.

    Built during discovery and stored in PackDescriptorRegistry.
    Resolution uses only these descriptors, never the filesystem.
    """
    # Identity / hierarchy
    localId: str
    packTreeId: str
    kind: PackKind
    
    # Author + version (declared vs effective)
    declaredAuthor: str | None
    declaredSemVerPackVersion: SemVerPackVersion | None
    effectiveAuthor: str
    effectiveSemVerPackVersion: SemVerPackVersion | None
    isVersionAgnostic: bool = False
    
    # Location and layer
    layer: LayerKind            # first-party / third-party / custom / saves
    baseRoot: Path              # Root under ContentRootsService
    packRoot: Path              # Directory containing this pack's manifest
    manifestPath: Path          # Full path to manifest file
    parent: "PackDescriptor | None" = None
    
    # Visibility / inheritance
    visibility: VisibilityKind
    importFromParent: bool
    exports: Mapping[str, object] | None
    
    # Compatibility hints (purely informational)
    recommendedPacks: Sequence[PackRequest] = field(default_factory=tuple)
    supportedPacks: Sequence[PackRequest] = field(default_factory=tuple)
    unsupportedPacks: Sequence[PackRequest] = field(default_factory=tuple)

    # Assets and runtime entries
    assets: Mapping[str, AssetMeta] = field(default_factory=dict)
    # e.g. { "python": [Path("service.py")], "javascript": [Path("ui.js")] }
    runtimeEntries: Mapping[str, Sequence[Path]] = field(default_factory=dict)
    
    # UI / descriptive metadata (from manifest or derived)
    # User-facing pack name. Defaults to manifest id when omitted.
    name: str
    description: str | None
    # Canonical author name (string) or None
    authorName: str | None
    
    # Full raw JSON/JSON5 manifest object.
    rawJson: Mapping[str, Any]



class PackDescriptorRegistry:
    """
    In-memory index of all discovered packs.
    
    Responsibilities:
      - Hold PackDescriptor instances discovered from roots.
      - Provide efficient lookup by packTreeId.
      - Provide SemVer-based resolution for a given
        (packTreeId[, kind][, author], requirement).
    
    SavePack preference:
      - Packs discovered under LayerKind.SAVES are preferred
        over content-layer packs when versions tie.
    """
    
    def __init__(self, metas: Iterable[PackDescriptor]):
        self._byTreeId: dict[str, list[PackDescriptor]] = defaultdict(list)
        self._all: list[PackDescriptor] = []
        
        for desc in metas:
            self.register(desc)
    
    # ----- Registration -----
    
    def register(self, desc: PackDescriptor) -> None:
        # Reject exact duplicates in the same layer
        for existing in self._byTreeId[desc.packTreeId]:
            if (
                existing.kind == desc.kind
                and existing.effectiveAuthor == desc.effectiveAuthor
                and existing.effectiveSemVerPackVersion == desc.effectiveSemVerPackVersion
                and existing.layer == desc.layer
            ):
                msg = (
                    "Duplicate pack descriptor: "
                    f"{desc.effectiveAuthor}@{desc.packTreeId}:"
                    f"{desc.effectiveSemVerPackVersion} in layer {desc.layer}"
                )
                raise ValueError(msg)
        
        self._byTreeId[desc.packTreeId].append(desc)
        self._all.append(desc)
        
        # Lifecycle: pack is now indexed in the registry.
        try:
            trace = getTracer()
            trace.traceEvent(
                "packs.lifecycle",
                attrs={
                    "phase": "indexed",
                    "packId": desc.localId,
                    "packTreeId": desc.packTreeId,
                    "kind": desc.kind.value,
                    "authorName": desc.authorName,
                    "effectiveAuthor": desc.effectiveAuthor,
                    "version": (
                        str(desc.declaredSemVerPackVersion)
                        if desc.declaredSemVerPackVersion is not None
                        else None
                    ),
                    "layer": desc.layer.value,
                    "baseRoot": str(desc.baseRoot),
                    "pickRoot": str(desc.packRoot),
                }
            )
        except Exception:
            # Tracing never break discovery.
            pass
    
    # ----- Basic iteration -----
    
    def all(self) -> tuple[PackDescriptor, ...]:
        return tuple(self._all)
    
    def findByTreeId(self, packTreeId: str) -> list[PackDescriptor]:
        return list(self._byTreeId.get(packTreeId, ()))
    
    # ----- Candidate lookup -----
    
    def findCandidates(
        self,
        *,
        packTreeId: str,
        kind: PackKind | None = None,
        author: str | None = None,
    ) -> list[PackDescriptor]:
        """
        Return all descriptors that match the given packTreeId, optionally
        filtered by kind and effectiveAuthor.
        """
        candidates = self._byTreeId.get(packTreeId, ())
        result: list[PackDescriptor] = []
        
        for desc in candidates:
            if kind is not None and desc.kind != kind:
                continue
            if author is not None and desc.effectiveAuthor != author:
                continue
            result.append(desc)
        
        return result
    
    # ----- SemVer resolution -----
    
    def resolveBest(
        self,
        *,
        packTreeId: str,
        kind: PackKind | None = None,
        author: str | None = None,
        requirement: SemVerPackRequirement | None = None,
        preferSaves: bool = True,
    ) -> PackDescriptor | None:
        """
        Resolve the "best" pack for (packTreeId[, kind][, author]) given
        an optional SemVer requirement.
        
        Rules:
          - If no candidates exist: returns None.
          - SemVer candidates = those with a usable effectiveSemVerPackVersion.
          - If requirement is not None and no SemVer candidates exist:
                returns None (cannot satisfy requirement).
          - If requirement is None and no SemVer candidates exist:
                returns the first candidate after layering/order sorting.
          - If SemVer candidates exist:
                uses SemVerResolver.matchCandidates, preserving candidate ordering
                so LayerKind.SAVES candidates win ties.
        """
        candidates = self.findCandidates(
            packTreeId=packTreeId,
            kind=kind,
            author=author,
        )
        if not candidates:
            return None
        
        # Layer preference: saves first if requested
        if preferSaves:
            candidates.sort(
                key=lambda desc: (
                    desc.layer != LayerKind.SAVES,
                    str(desc.baseRoot),
                    str(desc.packRoot),
                )
            )
        else:
            candidates.sort(
                key=lambda desc: (
                    str(desc.baseRoot),
                    str(desc.packRoot),
                )
            )
        
        semverCandidates: list[tuple[SemVerPackVersion, PackDescriptor]] = [
            (desc.effectiveSemVerPackVersion, desc)
            for desc in candidates
            if desc.effectiveSemVerPackVersion is not None
        ]
        
        if requirement is not None and not semverCandidates:
            # Caller asked for a version constraint and nothing is versioned
            return None
        
        if requirement is None and not semverCandidates:
            # No version info at all. Just use the first after layering ordering.
            return candidates[0]
        
        matchResult = SemVerResolver.matchCandidates(semverCandidates, requirement)
        if matchResult.best is None:
            return None
        
        _bestVersion, bestMeta = matchResult.best
        return bestMeta



# ------------------------------------------------------------------ #
# Manifest reading / normalization
# ------------------------------------------------------------------ #

def _findManifestPath(dirPath: Path) -> Path | None:
    for name in _MANIFEST_NAMES:
        candidate = dirPath / name
        if candidate.is_file():
            return candidate
    return None



def _loadManifestFile(path: Path) -> Mapping[str, Any]:
    if path.suffix == ".json5":
        rawJson = json5.loads(path.read_text(encoding="utf-8"))
    elif path.suffix == ".json":
        rawJson = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"Unknown manifest file extension '{path.suffix}'")
    if rawJson is None or not isinstance(rawJson, dict):
        raise ValueError(f"Manifest file '{path}' is not a JSON object")
    return rawJson



def _canonicalAuthorName(author: str | Mapping[str, Any] | None) -> str | None:
    if isinstance(author, str):
        name = author.strip()
        return name or None
    if isinstance(author, Mapping):
        nameVal = author.get("name")
        if isinstance(nameVal, str):
            name = nameVal.strip()
            return name or None
    return None



def _normalizePackDescriptor(
    *,
    rawJson: Mapping[str, Any],
    manifestPath: Path,
    baseRoot: Path,
    layer: LayerKind,
) -> PackDescriptor:
    packId = str(rawJson.get("id") or "").strip()
    if not packId or not _ID_RE.fullmatch(packId):
        raise ValueError(f"Invalid pack id {packId!r} in manifest {str(manifestPath)}")
    
    name = str(rawJson.get("name") or "").strip() or packId
    
    kindRaw = str(rawJson.get("kind") or "").strip()
    if not kindRaw:
        raise ValueError(f"Missing kind in manifest {str(manifestPath)}")
    try:
        kind = PackKind(kindRaw)
    except ValueError as err:
        raise ValueError(f"Unknown pack kind {kindRaw!r} in manifest {str(manifestPath)}") from err
        
    versionVal = rawJson.get("version")
    versionStr: str | None
    if isinstance(versionVal, str):
        versionStr = versionVal.strip() or None
    else:
        versionStr = None
    
    semver: SemVerPackVersion | None = None
    if versionStr:
        try:
            semver = parseSemVerPackVersion(versionStr)
        except Exception:
            semver = None

    authorRaw: str | Mapping[str, Any] | None = rawJson.get("author")
    if authorRaw is not None and not isinstance(authorRaw, (str, Mapping)):
        authorRaw = None
    authorName = _canonicalAuthorName(authorRaw)
    
    description = rawJson.get("description")
    if not isinstance(description, str):
        description = None
    
    # Declared vs effective author/version (no parent support yet)
    declaredAuthor = authorName
    declaredSemVerPackVersion = semver
    effectiveAuthor = declaredAuthor or "unknown"
    effectiveSemVerPackVersion = declaredSemVerPackVersion
    
    # Hierarchy: for now, discovery does not build nested trees, so packTreeId == localId
    packTreeId = packId
    
    packRoot = manifestPath.parent
    
    # Visibility defaults:
    visRaw = rawJson.get("visibility")
    if isinstance(visRaw, str):
        visRawNorm = visRaw.strip().lower()
        if visRawNorm == "public":
            visibility = VisibilityKind.PUBLIC
        elif visRawNorm == "private":
            visibility = VisibilityKind.PRIVATE
        else:
            visibility = VisibilityKind.PUBLIC if kind is PackKind.CONTENT else VisibilityKind.PRIVATE
    else:
        visibility = VisibilityKind.PUBLIC if kind is PackKind.CONTENT else VisibilityKind.PRIVATE
    
    # importFromParent defaults: false for viewPack, true otherwise
    importFromParentRaw = rawJson.get("importFromParent")
    if isinstance(importFromParentRaw, bool):
        importFromParent = importFromParentRaw
    else:
        importFromParent = False if kind is PackKind.VIEW else True
    
    exportsRaw = rawJson.get("exports")
    exports: Mapping[str, object] | None
    if isinstance(exportsRaw, Mapping):
        exports = exportsRaw
    else:
        exports = None

    # Compatibility hints and assets/runtimeEntries
    recommendedPacks: Sequence[PackRequest] = ()
    supportedPacks: Sequence[PackRequest] = ()
    unsupportedPacks: Sequence[PackRequest] = ()
    assets: Mapping[str, AssetMeta] = {}
    runtimeEntries: Mapping[str, Sequence[Path]] = {}
    
    return PackDescriptor(
        localId=packId,
        packTreeId=packTreeId,
        kind=kind,
        declaredAuthor=declaredAuthor,
        declaredSemVerPackVersion=declaredSemVerPackVersion,
        effectiveAuthor=effectiveAuthor,
        effectiveSemVerPackVersion=effectiveSemVerPackVersion,
        isVersionAgnostic=False,
        layer=layer,
        baseRoot=baseRoot,
        packRoot=packRoot,
        manifestPath=manifestPath,
        parent=None,
        visibility=visibility,
        importFromParent=importFromParent,
        exports=exports,
        recommendedPacks=recommendedPacks,
        supportedPacks=supportedPacks,
        unsupportedPacks=unsupportedPacks,
        assets=assets,
        runtimeEntries=runtimeEntries,
        name=name,
        description=description,
        authorName=authorName,
        rawJson=rawJson,
    )



# ------------------------------------------------------------------ #
# Discovery from roots
# ------------------------------------------------------------------ #

def _isRelativeTo(path: Path, base: Path) -> bool:
    return path.is_relative_to(base)



def _walkForPackDescriptors(
    dirPath: Path,
    *,
    baseResolved: Path,
    baseRoot: Path,
    layer: LayerKind,
    allowSymlinks: bool,
    pathStack: tuple[Path, ...],
    seen: set[Path],
    out: list[PackDescriptor],
) -> None:
    tracer = getTracer()
    
    try:
        if not dirPath.is_dir():
            try:
                tracer.traceEvent(
                    "packs.dirSkipped",
                    attrs={
                        "path": str(dirPath),
                        "reason": "notDir",
                    },
                    level="debug",
                    tags=["packs", "fs", "skip"],
                )
            except Exception:
                pass
            return
        
        if dirPath.is_symlink() and not allowSymlinks:
            try:
                tracer.traceEvent(
                    "packs.dirSkipped",
                    attrs={
                        "path": str(dirPath),
                        "reason": "symlinkNotAllowed",
                    },
                    level="debug",
                    tags=["packs", "fs", "skip"],
                )
            except Exception:
                pass
            return
        
        resolved = dirPath.resolve(strict=False)
        if not _isRelativeTo(resolved, baseResolved):
            try:
                tracer.traceEvent(
                    "packs.dirSkipped",
                    attrs={
                        "path": str(resolved),
                        "reason": "outsideBaseRoot",
                        "baseRoot": str(baseResolved),
                    },
                    level="debug",
                    tags=["packs", "fs", "skip"],
                )
            except Exception:
                pass
            return
    except Exception as exc:
        try:
            tracer.traceEvent(
                "packs.dirSkipped",
                attrs={
                    "path": str(dirPath),
                    "reason": "statError",
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc),
                },
                level="debug",
                tags=["packs", "fs", "skip"],
            )
        except Exception:
            pass
        return
    
    # Symlink / directory loop detection.
    if resolved in pathStack:
        if dirPath.is_symlink():
            logger.warning(
                "Detected symlink loop while scanning packs: '%s' (base '%s')",
                resolved,
                baseResolved,
            )
            try:
                tracer.traceEvent(
                    "packs.symlinkLoop",
                    attrs={
                        "resolvedPath": str(resolved),
                        "baseRoot": str(baseResolved),
                    },
                    level="warn",
                    tags=["packs", "fs", "symlink"],
                )
            except Exception:
                pass
        return
    
    nextStack = pathStack + (resolved,)
    
    manifestPath = _findManifestPath(dirPath)
    if manifestPath:
        manifestResolved = manifestPath.resolve()
        if manifestResolved in seen:
            return
        seen.add(manifestResolved)
        
        try:
            rawJson = _loadManifestFile(manifestPath)
            desc = _normalizePackDescriptor(
                rawJson=rawJson,
                manifestPath=manifestResolved,
                baseRoot=baseRoot,
                layer=layer,
            )
            
            try:
                attrs={
                    "packId": desc.localId,
                    "packName": desc.name,
                    "kind": desc.kind.value,
                    "authorName": desc.authorName,
                    "version": (
                        str(desc.declaredSemVerPackVersion)
                        if desc.declaredSemVerPackVersion is not None
                        else None
                    ),
                    "dir": str(desc.packRoot),
                    "manifestPath": str(desc.manifestPath),
                    "layer": desc.layer.value,
                    "baseRoot": str(baseResolved),
                }
                tracer.traceEvent(
                    "packs.manifestFound",
                    attrs=attrs,
                    level="info",
                    tags=["packs", "manifest"],
                )
                tracer.traceEvent(
                    "packs.lifecycle",
                    attrs={
                        **attrs,
                        "phase": "discoverd",
                    },
                    level="debug",
                    tags=["packs", "lifecycle"],
                )
            except Exception:
                pass
            
            out.append(desc)
        except Exception as exc:
            logger.exception("Failed to read manifest file '%s'", str(manifestPath))
            try:
                tracer.traceEvent(
                    "packs.manifestInvalid",
                    attrs={
                        "manifestPath": str(manifestPath),
                        "errorType": type(exc).__name__,
                        "errorMessage": str(exc),
                    },
                    level="warn",
                    tags=["packs", "manifest", "error"],
                )
            except Exception:
                pass
        # Do not descend below a pack root.
        return
    
    # No manifest - descend deeper.
    try:
        for child in dirPath.iterdir():
            _walkForPackDescriptors(
                child,
                baseResolved=baseResolved,
                baseRoot=baseRoot,
                layer=layer,
                allowSymlinks=allowSymlinks,
                pathStack=nextStack,
                seen=seen,
                out=out,
            )
    except Exception:
        return



def buildPackDescriptorRegistry() -> PackDescriptorRegistry:
    """
    Discover packs across all configured roots and build a PackDescriptorRegistry.
    
    Discovery rules:
      - Content packs:
          contentRootsService.contentRoots() are treated as base roots.
      - Save packs:
          contentRootsService.rootsFor("saves") are treated as save-layer base roots.
      - Each base root is scanned recursively:
          • Any directory containing a manifest.json5/json is a pack root.
          • Descent stops at a pack root (no nested packs within a pack).
          • Symlink loops are detected and skipped.
      - Save-layer packs are preferred over content-layer packs when resolving
        by SemVer if versions tie.
    """
    tracer = getTracer()
    allowSymlinks = configBool("roots.followSymlinks", False)
    contentRootsService = getContentRootsService()
    
    contentRoots = list(contentRootsService.contentRoots())
    saveRoots = list(contentRootsService.rootsFor("saves"))
    
    span = None
    try:
        span = tracer.startSpan(
            "packs.meta.scan",
            attrs={
                "contentRootCount": len(contentRoots),
                "saveRootCount": len(saveRoots),
                "allowSymlinks": allowSymlinks,
            },
            tags=["packs"],
        )
        tracer.traceEvent(
            "packs.meta.scan.start",
            level="info",
            tags=["packs"],
            span=span,
        )
    except Exception:
        span = None
    
    metas: list[PackDescriptor] = []
    seen: set[Path] = set()
    
    def _scanRoots(roots: Iterable[Path], layer: LayerKind) -> None:
        for base in roots:
            try:
                baseResolved = base.resolve(strict=False)
            except Exception:
                continue
            
            try:
                tracer.traceEvent(
                    "packs.meta.scan.root",
                    level="debug",
                    tags=["packs"],
                    attrs={
                        "layer": layer.value,
                        "baseRoot": str(baseResolved),
                    },
                    span=span,
                )
            except Exception:
                pass
            
            try:
                if not baseResolved.exists() or not baseResolved.is_dir():
                    continue
            except Exception:
                continue
            
            try:
                for child in baseResolved.iterdir():
                    _walkForPackDescriptors(
                        child,
                        baseResolved=baseResolved,
                        baseRoot=baseResolved,
                        layer=layer,
                        allowSymlinks=allowSymlinks,
                        pathStack=(),
                        seen=seen,
                        out=metas,
                    )
            except Exception:
                continue
    
    # Important: scan saves first, then content - so saves-layer candidates
    # appear earlier and win version ties in resolution.
    _scanRoots(saveRoots, LayerKind.SAVES)
    _scanRoots(contentRoots, LayerKind.FIRST_PARTY)
    
    if span is not None:
        try:
            tracer.traceEvent(
                "packs.meta.scan.done",
                level="info",
                tags=["packs"],
                span=span,
                attrs={
                    "packCount": len(metas),
                },
            )
            tracer.endSpan(
                span,
                status="ok",
                tags=["packs"],
            )
        except Exception:
            pass
    
    return PackDescriptorRegistry(metas)
