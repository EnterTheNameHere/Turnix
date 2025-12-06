# backend/mods/discover.py
from __future__ import annotations
import logging
from collections.abc import Iterable
from pathlib import Path
from threading import RLock
from typing import TypeAlias

import json5

from backend.app.globals import getTracer, getContentRootsService
from backend.content.packs import ResolvedPack, PackResolver
from backend.mods.manifest import ModManifest
from backend.mods.roots_registry import getRoots as getRegisteredRoots

logger = logging.getLogger(__name__)

__all__ = [
    "scanMods", "rescanMods", "scanModsForMount",
    "rescanModsForMount"
]

_SCAN_LOCK = RLock()

ModInfo: TypeAlias = tuple[Path, Path, ModManifest, str]
ModMap: TypeAlias = dict[str, ModInfo]



def _dedupe(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(Path(path).resolve(strict=False))
        if key not in seen:
            out.append(Path(path))
            seen.add(key)
    return out



def _loadManifest(manifestPath: Path) -> ModManifest | None:
    try:
        raw = json5.loads(manifestPath.read_text(encoding="utf-8"))
        return ModManifest.model_validate(raw)
    except Exception as err:
        logger.warning("Skipping mod manifest at '%s': %s", manifestPath, err, exc_info=True)
        return None



def _modSearchRoots(
    *,
    appPack: ResolvedPack | None,
    saveRoot: Path | None,
    extraRoots: Iterable[Path] | None,
) -> list[Path]:
    """
    Build a list of base roots for mod packs.
    
    These roots are passed to PackResolver/PackDescriptorRegistry to scope which
    pack directories are considered.
    
    Precedence (earlier wins on collisions):
      1) saveRoot         - per-save overrides (if present)
      2) appPack.rootDir  - app-local mods
      3) extraRoots       - e.g. viewPack.rootDir, other deep/local contexts
      4) global content roots (first-party, third-party, custom)
    """
    roots: list[Path] = []
    
    if saveRoot:
        # Treat saveRoot as a content root: mods live under saveRoot / "mods".
        roots.append(Path(saveRoot))
    
    if appPack:
        # Allow mods nested under the appPack root: appPack.rootDir / "mods".
        roots.append(appPack.rootDir)
    
    # Mount-specific or extra roots (e.g. viewPack roots).
    if extraRoots:
        roots.extend(extraRoots)
    
    # Global configured content roots (first-party, third-party, custom, ...)
    roots.extend(getContentRootsService().contentRoots())
    
    dedupedRoots = _dedupe(roots)
    
    # Trace root collisions (multiple entries resolving to the same path).
    try:
        tracer = getTracer()
        dropped = len(roots) - len(dedupedRoots)
        if dropped > 0:
            # Build collision groups: resolvedPath -> [originalPaths]
            resolvedMap: dict[str, list[str]] = {}
            for root in roots:
                resolved = str(Path(root).resolve(strict=False))
                resolvedMap.setdefault(resolved, []).append(str(root))
                
            collisionGroups = {
                resolved: originals
                for resolved, originals in resolvedMap.items()
                if len(originals) > 1
            }
            
            tracer.traceEvent(
                "mods.rootCollision",
                attrs={
                    "requestedRoots": [str(root) for root in roots],
                    "finalRoots": [str(root) for root in dedupedRoots],
                    "droppedCount": dropped,
                    "collisionGroups": collisionGroups,
                },
                level="debug",
                tags=["mods", "roots"],
            )
    except Exception:
        # Tracing must not break discovery.
        pass
    
    return dedupedRoots



def _rootPriority(roots: list[Path], packRoot: Path) -> int:
    """
    Compute precedence index for a packRoot based on the first matching base root.
    
    Earlier roots win on collision. If no root matches, returns len(roots).
    """
    for idx, base in enumerate(roots):
        if packRoot == base or packRoot.is_relative_to(base):
            return idx
    return len(roots)



def _sortKeyForPack(roots: list[Path], packRoot: Path, modId: str, version: str | None) -> tuple[int, str, str]:
    """
    Sorting key with precedence:
      1) Earlier roots first
      2) Then by folder name (case-insensitive)
      3) Then by id/version for determinism
    """
    priority = _rootPriority(roots, packRoot)
    return (priority, packRoot.name.lower(), f"{modId}:{version or ''}")



def scanMods(
    *,
    allowedIds: Iterable[str] | None = None,
    appPack: ResolvedPack | None = None,
    saveRoot: Path | None = None,
    extraRoots: Iterable[Path] | None = None,
) -> ModMap:
    """
    Returns a mapping:
      modId -> (root, moddir, manifest, manifestFileName)
    
    Only mods whose ids are in allowedIds are returned when the iterable is not None.
    
    Search order (earlier roots win on collisions):
      1) saveRoot subtree (if present)
      2) appPack.rootDir subtree (if present)
      3) extraRoots subtrees (mount-specific or viewPack-local)
      4) each configured content root subtree (first-party, third-party, custom)
    """
    tracer = getTracer()
    span = None
    
    allowedSet = set(allowedIds) if allowedIds is not None else None
    roots = _modSearchRoots(
        appPack=appPack,
        saveRoot=saveRoot,
        extraRoots=extraRoots,
    )
    
    try:
        span = tracer.startSpan(
            "mods.scan",
            attrs={
                "rootCount": len(roots),
                "allowed": sorted(allowedSet) if allowedSet is not None else [],
            },
            tags=["mods", "scan"],
        )
        tracer.traceEvent(
            "mods.scan.start",
            level="debug",
            tags=["mods", "scan"],
            span=span,
        )
    except Exception:
        span = None
    
    found: ModMap = {}

    with _SCAN_LOCK:
        try:
            resolver = PackResolver()
            discoveredPacks = resolver.listPacks(kinds={"mod"}, roots=roots)
            
            # Compute priorities
            discoveredPacks.sort(key=lambda pack: _sortKeyForPack(roots, pack.rootDir, pack.id, pack.version))
            
            for pack in discoveredPacks:
                # pack.manifestPath is the pack-level manifest, but we still validate
                # against the ModManifest schema.
                manifest = _loadManifest(pack.manifestPath)
                if not manifest:
                    continue
                
                if allowedSet is not None and manifest.id not in allowedSet:
                    # Mod is present but not in the allowedIds filter.
                    try:
                        tracer.traceEvent(
                            "mods.modFilteredByAllowedIds",
                            attrs={
                                "modId": manifest.id,
                                "version": getattr(manifest, "version", None),
                                "sourceRoot": str(pack.sourceRoot),
                                "modDir": str(pack.rootDir),
                                "manifestPath": str(pack.manifestPath),
                            },
                            level="debug",
                            tags=["mods", "filter"],
                            span=span,
                        )
                    except Exception:
                        pass
                    continue
                
                if manifest.id in found:
                    # Earlier roots (by precedence) already provided this mod.
                    existingRoot, existingDir, existingManifest, existingFileName = found[manifest.id]
                    try:
                        tracer.traceEvent(
                            "mods.modCollision",
                            attrs={
                                "modId": manifest.id,
                                "existingRoot": str(existingRoot),
                                "existingDir": str(existingDir),
                                "existingVersion": getattr(existingManifest, "version", None),
                                "existingManifestPath": str(existingDir / existingFileName),
                                "newSourceRoot": str(pack.sourceRoot),
                                "newDir": str(pack.rootDir),
                                "newVersion": getattr(manifest, "version", None),
                                "newManifestPath": str(pack.manifestPath),
                            },
                            level="warn",
                            tags=["mods", "collision"],
                            span=span,
                        )
                    except Exception:
                        pass
                    continue
                
                found[manifest.id] = (
                    pack.sourceRoot,
                    pack.rootDir,
                    manifest,
                    pack.manifestPath.name,
                )

                # Successful registration of this mod
                try:
                    tracer.traceEvent(
                        "mods.modRegistered",
                        attrs={
                            "modId": manifest.id,
                            "version": getattr(manifest, "version", None),
                            "sourceRoot": str(pack.sourceRoot),
                            "modDir": str(pack.rootDir),
                            "manifestPath": str(pack.manifestPath),
                        },
                        level="debug",
                        tags=["mods", "register"],
                        span=span,
                    )
                except Exception:
                    pass
            
            if span is not None:
                tracer.traceEvent(
                    "mods.scan.done",
                    level="debug",
                    tags=["mods", "scan"],
                    span=span,
                    attrs={"modCount": len(found)},
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["mods", "scan"],
                )
        except Exception as err:
            if span is not None:
                tracer.traceEvent(
                    "mods.scan.error",
                    level="error",
                    tags=["mods", "scan", "error"],
                    span=span,
                    attrs={
                        "errorType": type(err).__name__,
                        "errorMessage": str(err),
                    },
                )
                tracer.endSpan(
                    span,
                    status="error",
                    tags=["mods", "scan", "error"],
                    errorType=type(err).__name__,
                    errorMessage=str(err),
                )
            raise
    
    logger.info(
        "Mods discovered: %d (allowedIds=%s, rootCount=%s)",
        len(found),
        sorted(allowedSet) if allowedSet is not None else "*",
        len(roots),
    )
    return found



def rescanMods(
    *,
    allowedIds: Iterable[str] | None = None,
    appPack: ResolvedPack | None = None,
    saveRoot: Path | None = None,
    extraRoots: Iterable[Path] | None = None,
) -> ModMap:
    return scanMods(
        allowedIds=allowedIds,
        appPack=appPack,
        saveRoot=saveRoot,
        extraRoots=extraRoots,
    )



def scanModsForMount(
    viewKind: str,
    *,
    allowedIds: Iterable[str] | None = None,
    appPack: ResolvedPack | None = None,
    saveRoot: Path | None = None,
) -> ModMap:
    roots = getRegisteredRoots(viewKind)
    return scanMods(
        allowedIds=allowedIds,
        appPack=appPack,
        saveRoot=saveRoot,
        extraRoots=roots,
    )



def rescanModsForMount(
    viewKind: str,
    *,
    allowedIds: Iterable[str] | None = None,
    appPack: ResolvedPack | None = None,
    saveRoot: Path | None = None,
) -> ModMap:
    return scanModsForMount(
        viewKind,
        allowedIds=allowedIds,
        appPack=appPack,
        saveRoot=saveRoot,
    )
