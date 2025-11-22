# backend/mods/discover.py
from __future__ import annotations
import logging
from collections.abc import Iterable
from pathlib import Path
from threading import RLock
from typing import TypeAlias

import json5

from backend.app.globals import configBool, getTracer, getRootsService
from backend.content.packs import ResolvedPack
from backend.mods.manifest import ModManifest
from backend.mods.roots_registry import getRoots as getRegisteredRoots

logger = logging.getLogger(__name__)

__all__ = [
    "scanMods", "rescanMods", "findManifestPath",
    "scanModsForMount", "rescanModsForMount"
]

_IGNORE_DIRS = {".git", "node_modules", "__pycache__", "pytest_cache", ".vscode"}
_SCAN_LOCK = RLock()

ModInfo: TypeAlias = tuple[Path, Path, ModManifest, str]
ModMap: TypeAlias = dict[str, ModInfo]



def findManifestPath(path: Path) -> Path | None:
    p1, p2 = path / "manifest.json5", path / "manifest.json"
    if p1.is_file(): return p1
    if p2.is_file(): return p2
    return None



def _dedupe(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(Path(path).resolve())
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



def _scanRoot(
    root: Path,
    out: ModMap,
    *,
    allowedIds: set[str] | None,
    allowSymlinks: bool,
) -> None:
    try:
        rootResolved = root.resolve(strict=False)
    except Exception:
        return

    if not rootResolved.exists() or not rootResolved.is_dir():
        return

    try:
        entries = list(root.iterdir())
    except Exception as err:
        logger.debug("Skipping root '%s' due to error: %s", root, err, exc_info=True)
        return

    for child in entries:
        try:
            if child.name in _IGNORE_DIRS:
                continue
            if not allowSymlinks and child.is_symlink():
                continue
            if not child.is_dir():
                continue
            resolvedChild = child.resolve(strict=False)
            if not resolvedChild.is_relative_to(rootResolved): # Do not step out of root
                continue
        except Exception:
            continue

        manifestPath = findManifestPath(child)
        if not manifestPath:
            continue
        if not allowSymlinks and manifestPath.is_symlink():
            continue
        manifest = _loadManifest(manifestPath)
        if not manifest:
            continue
        if allowedIds is not None and manifest.id not in allowedIds:
            continue
        if manifest.id in out:
            # Higher priority root wins
            continue
        
        out[manifest.id] = (
            rootResolved,
            resolvedChild,
            manifest,
            manifestPath.name,
        )



def _modSearchRoots(
    *,
    appPack: ResolvedPack | None,
    saveRoot: Path | None,
    extraRoots: Iterable[Path] | None,
) -> list[Path]:
    roots: list[Path] = []
    if saveRoot:
        roots.append(Path(saveRoot) / "mods")
    if appPack:
        roots.append(appPack.rootDir / "mods")
    roots.extend((base / "mods") for base in getRootsService().packRoots())
    if extraRoots:
        roots.extend(extraRoots)
    return _dedupe(roots)



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
    Search order:
      1) saveRoot/mods (if present)
      2) appPack/mods (if present)
      3) each configured pack root's `mods` directory (first-party, third-party, custom)
      4) extraRoots (mount-specific overrides)
    """
    tracer = getTracer()
    span = None
    
    allowedSet = set(allowedIds) if allowedIds is not None else None
    roots = _modSearchRoots(
        appPack=appPack,
        saveRoot=saveRoot,
        extraRoots=extraRoots,
    )
    allowSymlinks = configBool("mods.allowSymlinks", False)
    
    try:
        span = tracer.startSpan(
            "mods.scan",
            attrs={
                "rootCount": len(roots),
                "allowSymlinks": allowSymlinks,
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
            for root in roots:
                _scanRoot(
                    root,
                    found,
                    allowedIds=allowedSet,
                    allowSymlinks=allowSymlinks
                )
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
        "Mods discovered: %d (allowSymlinks=%s, allowedIds=%s)",
        len(found),
        allowSymlinks,
        sorted(allowedSet) if allowedSet is not None else "*",
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
    mountId: str,
    *,
    allowedIds: Iterable[str] | None = None,
    appPack: ResolvedPack | None = None,
    saveRoot: Path | None = None,
) -> ModMap:
    roots = getRegisteredRoots(mountId)
    return scanMods(
        allowedIds=allowedIds,
        appPack=appPack,
        saveRoot=saveRoot,
        extraRoots=roots,
    )



def rescanModsForMount(
    mountId: str,
    *,
    allowedIds: Iterable[str] | None = None,
    appPack: ResolvedPack | None = None,
    saveRoot: Path | None = None,
) -> ModMap:
    return scanModsForMount(
        mountId,
        allowedIds=allowedIds,
        appPack=appPack,
        saveRoot=saveRoot,
    )
