# backend/content/pack_meta.py

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
    "PackDescriptor",
    "PackDescriptorRegistry",
    "buildPackDescriptorRegistry",
]



_MANIFEST_NAMES: tuple[str, ...] = ("manifest.json5", "manifest.json")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")



RootLayer = Literal["content", "saves"]



@dataclass(frozen=True)
class PackDescriptor:
    """
    Canonical, generalized representation of a single pack.
    """
    id: str
    name: str
    kind: str
    # Plain version string from manifest, or None if missing/empty.
    version: str | None
    # Parsed semantic version if version is SemVer, else None.
    semver: SemVerPackVersion | None
    # Canonical author name (string) or None
    authorName: str | None
    description: str | None
    # Where this pack came from: content roots (first-party/third-party/custom) or saves.
    rootLayer: RootLayer
    # Base root under which the pack was discovered (e.g. a single content root or a saves root)
    baseRoot: Path
    # Directory that contains the manifest - the "pack root".
    packRoot: Path
    # Full manifest file path.
    manifestPath: Path
    # Full raw JSON/JSON5 manifest object.
    rawJson: Mapping[str, Any]
    
    def hasSemVer(self) -> bool:
        return self.semver is not None



class PackDescriptorRegistry:
    """
    In-memory index of all discovered packs.
    
    Responsibilities:
      - Hold PackDescriptor instances discovered from roots.
      - Provide efficient lookup by (kind, id[, authorName]).
      - Provide SemVer-based resolution for a given (kind, id[, author], requirement)
    
    SavePack preference:
      - Packs discovered under "saves" roots (rootLayer == "saves") are preferred
        over content-layer packs when versions tie.
    """
    
    def __init__(self, metas: Iterable[PackDescriptor]):
        metasTuple = tuple(metas)
        self._metas: tuple[PackDescriptor, ...] = metasTuple
        
        # (kind, id) -> [PackDescriptor, ...]
        self._byKindId: dict[tuple[str, str], list[PackDescriptor]] = {}
        # (kind, authorName, id) -> [PackDescriptor, ...]
        self._byKindAuthorId: dict[tuple[str, str, str], list[PackDescriptor]] = {}
        
        for meta in metasTuple:
            key = (meta.kind, meta.id)
            self._byKindId.setdefault(key, []).append(meta)
            
            if meta.authorName:
                akey = (meta.kind, meta.authorName, meta.id)
                self._byKindAuthorId.setdefault(akey, []).append(meta)
    
    # ----- Basic iteration -----
    
    def all(self) -> tuple[PackDescriptor, ...]:
        return self._metas
    
    def iterByKind(self, kind: str) -> Iterable[PackDescriptor]:
        return (meta for meta in self._metas if meta.kind == kind)
    
    # ----- Candidate lookup -----
    
    def _candidates(
        self,
        kind: str,
        packId: str,
        *,
        author: str | None = None,
        preferSaves: bool = True,
    ) -> list[PackDescriptor]:
        if author:
            key = (kind, author, packId)
            candidates = list(self._byKindAuthorId.get(key, ()))
        else:
            key = (kind, packId)
            candidates = list(self._byKindId.get(key, ()))
        
        if not candidates:
            return []
        
        if preferSaves:
            # Save-layer packs first, then content-layer.
            candidates.sort(key=lambda meta: (meta.rootLayer != "saves", str(meta.baseRoot), str(meta.packRoot)))
        else:
            candidates.sort(key=lambda meta: (str(meta.baseRoot), str(meta.packRoot)))
        
        return candidates
    
    # ----- SemVer resolution -----
    
    def resolveBest(
        self,
        kind: str,
        packId: str,
        *,
        author: str | None = None,
        requirement: SemVerPackRequirement | None = None,
        preferSaves: bool = True,
    ) -> PackDescriptor | None:
        """
        Resolve the "best" pack for (kind, packId[, author]) given an optional SemVer requirement.
        
        Rules:
          - If no candidates exist: returns None.
          - SemVer candidates = those with meta.semver != None.
          - If requirement is not None and no SemVer candidates exist:
                returns None (cannot satisfy requirement).
          - If requirement is None and no SemVer candidates exist:
                returns the first candidate after layering/order sorting.
          - If SemVer candidates exist:
                uses SemVerResolver.matchCandidates, preserving candidate ordering
                so SavePack-layer candidates win ties.
        """
        candidates = self._candidates(kind, packId, author=author, preferSaves=preferSaves)
        if not candidates:
            return None
        
        semverCandidates: list[tuple[SemVerPackVersion, PackDescriptor]] = [
            (meta.semver, meta) for meta in candidates if meta.semver is not None
        ]
        
        if requirement is not None and not semverCandidates:
            return None
        
        if requirement is None and not semverCandidates:
            # No SemVer. Fall back to first candidate (after layering / baseRoot ordering)
            return candidates[0]
        
        # SemVer-based resolution
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



def _canonicalAuthorName(author: str | dict[str, Any] | None) -> str | None:
    if isinstance(author, str):
        name = author.strip()
        return name or None
    if isinstance(author, dict):
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
    rootLayer: RootLayer,
) -> PackDescriptor:
    packId = str(rawJson.get("id") or "").strip()
    if not packId or not _ID_RE.fullmatch(packId):
        raise ValueError(f"Invalid pack id {packId!r} in manifest {str(manifestPath)}")
    
    name = str(rawJson.get("name") or "").strip() or packId
    
    kind = str(rawJson.get("kind") or "").strip()
    if not kind:
        raise ValueError(f"Missing kind in manifest {str(manifestPath)}")
    
    versionVal = rawJson.get("version")
    if isinstance(versionVal, str):
        version = versionVal.strip() or None
    else:
        version = None
    
    author: str | dict[str, Any] | None = rawJson.get("author")
    if author is not None and not isinstance(author, (str, dict)):
        author = None
    
    authorName = _canonicalAuthorName(author)
    
    description = rawJson.get("description")
    if not isinstance(description, str):
        description = None
    
    semver: SemVerPackVersion | None = None
    if version:
        try:
            semver = parseSemVerPackVersion(version)
        except Exception:
            semver = None
    
    packRoot = manifestPath.parent
    
    return PackDescriptor(
        id=packId,
        name=name,
        kind=kind,
        version=version,
        semver=semver,
        authorName=authorName,
        description=description,
        rootLayer=rootLayer,
        baseRoot=baseRoot,
        packRoot=packRoot,
        manifestPath=manifestPath,
        rawJson=rawJson,
    )



# ------------------------------------------------------------------ #
# Discovery from roots
# ------------------------------------------------------------------ #

def _isRelativeTo(path: Path, base: Path) -> bool:
    try:
        return path.is_relative_to(base)
    except AttributeError:
        # Python < 3.9 fallback
        try:
            path.relative_to(base)
            return True
        except Exception:
            return False



def _walkForPackDescriptors(
    dirPath: Path,
    *,
    baseResolved: Path,
    baseRoot: Path,
    rootLayer: RootLayer,
    allowSymlinks: bool,
    pathStack: tuple[Path, ...],
    seen: set[tuple[str, str | None, str | None, Path]],
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
        try:
            rawJson = _loadManifestFile(manifestPath)
            meta = _normalizePackDescriptor(
                rawJson=rawJson,
                manifestPath=manifestPath.resolve(),
                baseRoot=baseRoot,
                rootLayer=rootLayer,
            )
            key = (meta.id, meta.authorName, meta.version, meta.packRoot)
            if key in seen:
                return
            seen.add(key)
            
            try:
                tracer.traceEvent(
                    "packs.manifestFound",
                    attrs={
                        "packId": meta.id,
                        "packName": meta.name,
                        "kind": meta.kind,
                        "authorName": meta.authorName,
                        "version": meta.version,
                        "dir": str(meta.packRoot),
                        "manifestPath": str(meta.manifestPath),
                        "rootLayer": rootLayer,
                        "baseRoot": str(baseResolved),
                    },
                    level="info",
                    tags=["packs", "manifest"],
                )
            except Exception:
                pass
            
            out.append(meta)
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
                rootLayer=rootLayer,
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
    seen: set[tuple[str, str | None, str | None, Path]] = set()
    
    def _scanRoots(roots: Iterable[Path], rootLayer: RootLayer) -> None:
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
                        "rootLayer": rootLayer,
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
                        rootLayer=rootLayer,
                        allowSymlinks=allowSymlinks,
                        pathStack=(),
                        seen=seen,
                        out=metas,
                    )
            except Exception:
                continue
    
    # Important: scan saves first, then content - so saves-layer candidates
    # appear earlier and win version ties in resolution.
    _scanRoots(saveRoots, "saves")
    _scanRoots(contentRoots, "content")
    
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
