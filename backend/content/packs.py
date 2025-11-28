# backend/content/packs.py
from __future__ import annotations
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json5

from backend.app.globals import configBool, getRootsService, getTracer
from backend.core.schema_registry import SEMVER_PATTERN_RE

logger = logging.getLogger(__name__)

__all__ = [
    "PackManifest", "ResolvedPack", "PackResolver",
    "parseQualifiedPackId", "PACK_KIND_DIRS",
]

_MANIFEST_NAMES = ("manifest.json5", "manifest.json")
PACK_KIND_DIRS: Mapping[str, str] = {
    "appPack": "appPacks",
    "viewPack": "viewPacks",
    "contentPack": "contentPacks",
    "mod": "mods",
}
_KNOWN_PACK_KINDS = frozenset((*PACK_KIND_DIRS.keys(), "savePack"))

_ID_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
_AUTHOR_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Qualified id grammar: author@id:version(:subpath)?
# - author optional (if missing → any author)
# - version optional (if missing → latest)
# - subpath optional (reserved for future filtering)
_QID_RE = re.compile(
    r"^(?:(?P<author>[A-Za-z0-9_-]+)@)?(?P<id>[A-Za-z0-9_-]+)(?::(?P<version>[^\s:\/\\]+))?(?::(?P<rest>.*))?$"
)



@dataclass(frozen=True)
class PackManifest:
    id: str
    name: str
    version: str
    author: str | None = None
    kind: str = "appPack" # "mod", "contentPack", ...
    rawJson: dict[str, Any] | None = None
    # Optional metadata bag (languages, images dir, etc.)
    meta: dict[str, Any] | None = None



@dataclass(frozen=True)
class ResolvedPack:
    """
    A fully resolved pack location.
    """
    id: str
    name: str
    version: str
    kind: str
    rootDir: Path       # Content directory root (the "pack directory")
    manifestPath: Path
    sourceRoot: Path    # The root under which the pack was discovered
    rawJson: dict[str, Any] | None
    author: str | None = None



def _readManifest(dirpath: Path) -> PackManifest | None:
    for filename in _MANIFEST_NAMES:
        path = dirpath / filename
        if not path.exists() or not path.is_file():
            continue
        try:
            if path.suffix == ".json5":
                raw = json5.loads(path.read_text(encoding="utf-8"))
            elif path.suffix == ".json":
                raw = json.loads(path.read_text(encoding="utf-8"))
            else:
                raise ValueError(f"Unknown manifest file extension '{path.suffix}'")
            if raw is None or not isinstance(raw, dict):
                raise ValueError(f"Manifest file '{path}' is not a JSON object")

            packId = str(raw.get("id") or "").strip()
            displayName = str(raw.get("displayName") or "").strip()
            version = str(raw.get("version") or "").strip()
            author = raw.get("author")
            kind = str(raw.get("kind") or "").strip()
            metaRaw = raw.get("meta")
            meta = metaRaw if isinstance(metaRaw, dict) else {}
            
            if "@" in packId:
                parts = packId.split("@", 1)
                if not author:
                    author = parts[0] or None
                elif parts[0] and author != parts[0]:
                    logger.warning(
                        "Author mismatch: manifest declares '%s', but qualified ID uses '%s'. "
                        "Qualified ID takes precedence.",
                        author, parts[0],
                    )
                    author = parts[0]
                
                packId = parts[1] if len(parts) > 1 else packId
            
            if not packId or not _ID_RE.fullmatch(packId):
                raise ValueError(f"Invalid packId: '{packId}'. Manifest path: {str(path)}")
            if author is not None and (not isinstance(author, str) or not _AUTHOR_RE.fullmatch(author)):
                author = None
            if not kind or kind not in _KNOWN_PACK_KINDS:
                raise ValueError(f"kind must be one of [{', '.join(repr(key) for key in _KNOWN_PACK_KINDS)}]. Got {kind!r} instead.")
            if not version or not SEMVER_PATTERN_RE.match(version):
                # Allow non-semver now
                pass
            manifest = PackManifest(
                id=packId,
                name=displayName or packId,
                version=version,
                author=author,
                kind=kind,
                meta=meta,
                rawJson=raw,
            )
            return manifest
        except Exception as exc:
            logger.exception("Failed to read manifest file %s", path)
            try:
                tracer = getTracer()
                tracer.traceEvent(
                    "packs.manifestInvalid",
                    attrs={
                        "manifestPath": str(path),
                        "errorType": type(exc).__name__,
                        "errorMessage": str(exc),
                    },
                    level="warn",
                    tags=["packs", "manifest", "error"],
                )
            except Exception:
                # Tracing must not break manifest reading
                pass
            return None
    return None
            


def parseQualifiedPackId(qid: str) -> tuple[str | None, str, str | None, str | None]:
    """
    Parses "author@id:version:rest" → (author|None, id, version|None, rest|None)
    This stub intentionally does not interpret :rest beyond capturing it.
    """
    matched = _QID_RE.match((qid or "").strip())
    if not matched:
        raise ValueError(f"Invalid qualified pack id: '{qid}'")
    author = matched.group("author")
    packId = matched.group("id")
    version = matched.group("version")
    rest = matched.group("rest")
    return author, packId, version, rest



class PackScanner:
    """
    Low-level pack scanner. Walks roots and discovers ResolvedPacks.
    """
    def __init__(self, *, allowSymlinks: bool):
        self.allowSymlinks = allowSymlinks
        self.tracer = getTracer()
    
    def findManifestPath(self, dirPath: Path) -> Path:
        for path in ((dirPath / name) for name in _MANIFEST_NAMES):
            if path.is_file():
                return path
        raise FileNotFoundError(f"No manifest found in '{dirPath}'")
    
    def walkForManifests(
        self,
        dirPath: Path,
        baseResolved: Path,
        *,
        rootIndex: int,
        packKind: str,
        kinds: set[str] | None,
        seen: set[tuple[str, str | None, str, Path]],
        tempOut: list[tuple[int, str, ResolvedPack]],
        pathStack: tuple[Path, ...],
    ) -> None:
        """
        Recursively walk directories until a manifest is found.
        If a directory has a manifest, treat it as a pack root and stop descending.
        """
        try:
            if not dirPath.is_dir():
                try:
                    self.tracer.traceEvent(
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
            if dirPath.is_symlink() and not self.allowSymlinks:
                try:
                    self.tracer.traceEvent(
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
            # Guard against escaping the discovery root
            if not resolved.is_relative_to(baseResolved):
                try:
                    self.tracer.traceEvent(
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
                self.tracer.traceEvent(
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
        
        # Symlink / directory loop detection: if this resolved path is already in the
        # current stack, bail out to avoid infinite recursion.
        if resolved in pathStack:
            if dirPath.is_symlink():
                logger.warning("Detected symlink loop while scanning packs: '%s' (base '%s')", resolved, baseResolved)
                try:
                    self.tracer.traceEvent(
                        "packs.symlinkLoop",
                        attrs={
                            "resolvedPath": str(resolved),
                            "baseRoot": str(baseResolved),
                        },
                        level="warn",
                        tags=["packs", "fs", "symlink"],
                    )
                except Exception:
                    # Tracing must not break scanning
                    pass
            return
        
        nextStack = pathStack + (resolved,)
        
        manifest = _readManifest(dirPath)
        if manifest:
            if manifest.kind == packKind and (not kinds or manifest.kind in kinds):
                key = (manifest.id, manifest.author, manifest.version, resolved)
                if key not in seen:
                    seen.add(key)
                    try:
                        manifestPath = self.findManifestPath(dirPath).resolve()
                    except Exception:
                        return
                    
                    # --- Tracing: manifest found ---
                    try:
                        self.tracer.traceEvent(
                            "packs.manifestFound",
                            attrs={
                                "packId": manifest.id,
                                "packName": manifest.name,
                                "kind": manifest.kind,
                                "author": manifest.author,
                                "version": manifest.version,
                                "dir": str(resolved),
                                "manifestPath": str(manifestPath),
                                "rootIndex": rootIndex,
                                "sourceRoot": str(baseResolved),
                            },
                            level="info",
                            tags=["packs", "manifest"],
                        )
                    except Exception:
                        # Do not break scanning if tracing fails
                        pass
                    
                    tempOut.append((
                        rootIndex,
                        dirPath.name.lower(),
                        ResolvedPack(
                            id=manifest.id,
                            name=manifest.name,
                            author=manifest.author,
                            version=manifest.version,
                            kind=manifest.kind,
                            rootDir=resolved,
                            manifestPath=manifestPath,
                            sourceRoot=baseResolved,
                            rawJson=manifest.rawJson,
                        ),
                    ))
            else:
                # Manifest present but not used due to kind or filter mismatch
                reason = "kindMismatch"
                if kinds is not None and manifest.kind not in kinds:
                    reason = "filteredByKinds"
                try:
                    self.tracer.traceEvent(
                        "packs.packRootIgnored",
                        attrs={
                            "packId": manifest.id,
                            "packName": manifest.name,
                            "kind": manifest.kind,
                            "author": manifest.author,
                            "version": manifest.version,
                            "dir": str(resolved),
                            "rootIndex": rootIndex,
                            "sourceRoot": str(baseResolved),
                            "expectedPackKind": packKind,
                            "kindsFilter": sorted(kinds) if kinds else None,
                            "reason": reason,
                        },
                        level="debug",
                        tags=["packs", "manifest", "ignored"],
                    )
                except Exception:
                    pass
            # Stop descent - this directory is a pack root
            return
        
        # No manifest → descent deeper
        try:
            for subDir in dirPath.iterdir():
                self.walkForManifests(
                    subDir,
                    baseResolved,
                    rootIndex=rootIndex,
                    packKind=packKind,
                    kinds=kinds,
                    seen=seen,
                    tempOut=tempOut,
                    pathStack=nextStack,
                )
        except Exception:
            return

    def scanPacks(self, *, roots: list[Path], kinds: set[str] | None = None) -> list[ResolvedPack]:
        tempOut: list[tuple[int, str, ResolvedPack]] = []
        seen: set[tuple[str, str | None, str, Path]] = set() # (id, author, version, rootDir)

        for rootIndex, base in enumerate(roots):
            try:
                baseResolved = base.resolve(strict=False)
            except Exception:
                continue
            
            for packKind, packDirName in PACK_KIND_DIRS.items():
                packRoot = base / packDirName
                if not packRoot.exists() or not packRoot.is_dir():
                    continue
            
                # Recursive pack discovery
                try:
                    for child in packRoot.iterdir():
                        self.walkForManifests(
                            child,
                            baseResolved,
                            rootIndex=rootIndex,
                            packKind=packKind,
                            kinds=kinds,
                            seen=seen,
                            tempOut=tempOut,
                            pathStack=(),
                        )
                except Exception:
                    continue
        
        # Sort by (root precedence, folder name) to be deterministically but keep precedence.
        tempOut.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in tempOut]



class PackResolver:
    """
    Scans content roots (first-party/, third-party/, custom/) for content directories,
    reads manifest, and resolved by author/id/version with precedence by roots order.
    """
    def listPacks(self, *, kinds: set[str] | None = None, roots: list[Path] | None = None) -> list[ResolvedPack]:
        """
        List packs of given kinds across one or more base roots.
        
        - If roots is None, scan the configured pack roots (first-party, third-party, custom).
        - If roots is provided, scan only those base directories.
        - Discovery always:
            • descends thought directories without a manifest
            • stops descending at a directory that has a manifest (treat it as a pack root)
        """
        rootsService = getRootsService()
        allowSymlinks = configBool("roots.followSymlinks", False)
        if roots is None:
            # Default: all pack-hosting content roots (first-party, third-party, custom)
            roots = list(rootsService.contentRoots())
        else:
            # Normalize and dedupe given roots
            normRoots: list[Path] = []
            seen: set[str] = set()
            for base in roots:
                resolved = str(Path(base).expanduser().resolve(strict=False))
                if resolved in seen:
                    continue
                seen.add(resolved)
                normRoots.append(Path(resolved))
            roots = normRoots
        
        
        tracer = getTracer()
        span = tracer.startSpan(
            "packs.scan",
            attrs={
                "kinds": sorted(kinds) if kinds else None,
                "rootCount": len(roots),
            },
            level="info",
            tags=["packs"],
        )
        
        try:
            scanner = PackScanner(allowSymlinks=allowSymlinks)
            packs = scanner.scanPacks(roots=roots, kinds=kinds)
            tracer.endSpan(
                span,
                status="ok",
                attrs={
                    "packCount": len(packs),
                },
            )
            return packs
        except Exception as exc:
            tracer.endSpan(
                span,
                status="error",
                errorType=type(exc).__name__,
                errorMessage=str(exc),
            )
            raise

    def resolvePack(
        self,
        kind: str,
        qidOrId: str,
        *,
        roots: list[Path] | None = None,
    ) -> ResolvedPack | None:
        """
        Resolve a pack of a given kind by:
          - exact author@id:version
          - or "id:version"
          - or "author@id"
          - or "id" (select latest by version if comparable, otherwise first by roots precedence)
        
        If roots is provided, only packs discovered under those base directories
        are considered. Otherwise, all configured pack roots are scanned.
        """
        # Discover packs of requested kind in the requested roots (or global).
        packs = [
            pack for pack in self.listPacks(kinds={kind}, roots=roots)
        ]
        candidates, version, packId = self._filterCandidatesByQualifiedId(packs, qidOrId)
        if not candidates:
            return None
        if version:
            # Prefer exact version
            exact = [pack for pack in candidates if pack.version == version]
            if exact:
                return exact[-1] # Last is fine as roots are already precedence-ordered.
            # TODO: add SemVer choice here when ready
            return None
        # No version → pick "latest" by SemVer if possible, else pick last by roots order.
        def semverKey(version: str) -> tuple[int, int, int, int]:
            matched = SEMVER_PATTERN_RE.match(version or "")
            if not matched:
                return (0, 0, 0, 0)
            major = int(matched.group("major"))
            minor = int(matched.group("minor"))
            patch = int(matched.group("patch"))
            isStable = 1 if (matched.group("prerelease") is None) else 0
            return (major, minor, patch, isStable)
        candidates.sort(key=lambda pack: semverKey(pack.version))
        return candidates[-1]

    def resolveAppPack(self, qidOrId: str) -> ResolvedPack | None:
        """
        Convenience wrapper for resolvePack(kind="appPack").
        """
        return self.resolvePack("appPack", qidOrId)
    
    def resolveViewPack(
        self,
        qidOrId: str,
        *,
        roots: list[Path] | None = None,
    ) -> ResolvedPack | None:
        """
        Resolve a viewPack by qualified id in an optional scope.
        
        Typical usage:
          - Global: resolveViewPack("testView")
          - Inside an appPack: resolveViewPack("testView", roots=[appPack.rootDir])
        """
        return self.resolvePack("viewPack", qidOrId, roots=roots)
    
    def resolveViewPackForApp(
        self,
        appPack: ResolvedPack,
        viewKind: str | None,
    ) -> ResolvedPack | None:
        """
        Resolve a viewPack for a given appPack + viewKind with these rules:
        
            - viewKind is normalized to a non-empty string, defaulting to "main".
            - AppPack can declare which viewPacks it owns via meta.viewPacks
              in its manifest (list | dict | string).
            - If viewKind == "main":
                • Only ever resolved from inside the appPack (roots=[appPack.rootDir]).
                • Never resolved from global roots, so nothing external can override it.
                • If not found locally, returns None (no viewPack → plain appPack mods).
            - If viewKind != "main":
                • If declared in appPack.meta.viewPacks → resolve locally only
                  (roots=[appPack.rootDir]). No global fallback.
                • If NOT declared in appPack.meta.viewPacks → try global viewPacks
                  (roots=None), which means any external viewPack with that id
                  can be used.
        """
        viewKind = (viewKind or "main").strip() or "main"
        
        raw = appPack.rawJson or {}
        meta = raw.get("meta") if isinstance(raw, dict) else None
        
        declared: set[str] = set()
        if isinstance(meta, dict):
            metaViewPacks = meta.get("viewPacks")
            if isinstance(metaViewPacks, str):
                name = metaViewPacks.strip()
                if name:
                    declared.add(name)
            elif isinstance(metaViewPacks, list):
                for item in metaViewPacks:
                    name = str(item or "").strip()
                    if name:
                        declared.add(name)
            elif isinstance(metaViewPacks, dict):
                for key in metaViewPacks.keys():
                    name = str(key or "").strip()
                    if name:
                        declared.add(name)
        
        # Special handling for "main":
        #   - Do NOT search globally (no override of default main).
        #   - Only resolve from inside the appPack. If not found, return None.
        if viewKind == "main":
            local = self.resolveViewPack(viewKind, roots=[appPack.rootDir])
            return local
        
        # Non-main viewKind:
        # If the appPack declares this viewPack, we treat it as owned
        # and resolve ONLY from inside the appPack.
        if viewKind in declared:
            return self.resolveViewPack(viewKind, roots=[appPack.rootDir])

        # AppPack does not declare this viewKind → allow external/global viewPack.
        # This is the "check viewPacks out of the appPack" case.
        return self.resolveViewPack(viewKind)
    
    def _filterCandidatesByQualifiedId(
        self,
        packs: list[ResolvedPack],
        qidOrId: str
    ) -> tuple[list[ResolvedPack], str | None, str]:
        """
        Apply qualified-id filtering (author@id:version) to a list of packs.
        Returns (candidates, version, packId).
        """
        author, packId, version, _rest = parseQualifiedPackId(qidOrId)
        candidates = [
            pack
            for pack in packs
            if pack.id == packId and (author is None or pack.author == author)
        ]
        return candidates, version, packId
