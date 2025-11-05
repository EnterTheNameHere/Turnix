# backend/mods/discover.py
from __future__ import annotations
import os
import json5
import logging
from collections.abc import Iterable
from pathlib import Path
from threading import RLock
from typing import Any, Mapping # pyright: ignore[reportShadowedImports]

from backend.app.globals import configBool
from backend.app.paths import ROOT_DIR
from backend.mods.manifest import ModManifest
from backend.mods.roots_registry import getRoots as getRegisteredRoots

logger = logging.getLogger(__name__)

__all__ = [
    "defaultModRoots", "MOD_ROOTS", "scanMods",
    "rescanMods", "findManifestPath", "scanModsForMount",
    "rescanModsForMount"
]



def defaultModRoots() -> list[tuple[Path, Mapping[str, Any]]]:
    """
    Returns the ordered list of mod roots. First repo mods/, then user mods folder.
    """
    roots: list[tuple[Path, Mapping[str, Any]]] = []
    roots.append((ROOT_DIR / "mods", {"writable": False, "trust": "unsigned-ok"}))
    
    usermods = Path(os.path.expanduser("~/Documents/My Games/Turnix/mods"))
    usermods.mkdir(parents=True, exist_ok=True)
    roots.append((usermods, {"writable": True, "trust": "unsigned-ok"}))
    return roots



# Key: tuple[str, ...] of resolved root paths ('' means "use defaults")
_SCAN_CACHE: dict[tuple[str, ...], dict[str, tuple[Path, Path, ModManifest, str]]] = {}
_SCAN_LOCK = RLock()
MOD_ROOTS = defaultModRoots()
IGNORE_DIRS = {".git", "node_modules", "__pycache__"}



def _normalizeRoots(overrideRoots: Iterable[Path] | None) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """
    Returns (rootsTuple, cacheKey)
    When overrideRoots is None → use MOD_ROOTS and cacheKey = ('',)
    """
    if overrideRoots is None:
        roots = tuple(root for (root, _cfg) in MOD_ROOTS)
        key = ('',) # Default sentinel
        return roots, key
    rootsTuple = tuple(Path(root).resolve() for root in overrideRoots)
    key = tuple(str(root) for root in rootsTuple)
    return rootsTuple, key



def _scanDir(
    root: Path,
    parent: Path,
    out: dict[str, tuple[Path, Path, ModManifest, str]],
    *,
    allowSymlinks: bool,
) -> None:
    """
    Recursively discover mods under `root`, honoring the symlink policy:
    - If allowSymlinks == False:
      • Do NOT descend into symlink directories
      • Do NOT accept manifests that are symlinks
    - Always ensure discovered directories remain within root
    """
    # Hard guard: parent must remain within the root
    try:
        rootResolved = root.resolve(strict=True)        # Must exist
        parentResolved = parent.resolve(strict=False)   # May not exist yet
    except Exception:
        return
    
    if not parentResolved.is_relative_to(rootResolved):
        return
    
    # If parent itself is a symlink and symlinks are not allowed, stop here.
    if not allowSymlinks and parent.is_symlink():
        return

    # If this directory contains a manifest, validate it and stop descending.
    manifestPath = findManifestPath(parent)
    if manifestPath:
        # Skip manifest if symlink disallowed and this file is a symlink
        if not allowSymlinks and manifestPath.is_symlink():
            return
        
        try:
            raw = json5.loads(manifestPath.read_text(encoding="utf-8"))
            manifest = ModManifest.model_validate(raw)
        except Exception as err:
            logger.warning("Skipping mod at '%s' due to manifest error: %s", parent, err)
            return
        
        if manifest.id in out:
            logger.warning(
                "Mod id '%s' at '%s' has same id as existing mod at '%s'. Skipping this one.",
                manifest.id, str(parent), str(out[manifest.id][1])
            )
            return
        
        out[manifest.id] = (root, parent, manifest, manifestPath.name)
        return
    
    # No manifest -> recurse into children
    try:
        entries = list(parent.iterdir())
    except Exception as err:
        logger.debug("Skipping '%s' as iterdir failed: %s", parent, err)
        return

    for ch in entries:
        try:
            name = ch.name
            if name in IGNORE_DIRS:
                continue
            if not ch.is_dir():
                continue
            if not allowSymlinks and ch.is_symlink():
                continue
        except Exception:
            # If any error occurs for this entry, just skip it
            continue

        _scanDir(root, ch, out, allowSymlinks=allowSymlinks)



def scanMods(overrideRoots: Iterable[Path] | None = None) -> dict[str, tuple[Path, Path, ModManifest, str]]:
    """
    Returns a mapping:
      modId -> (root, moddir, manifest, manifestFileName)
    Results are cached per root-set. Use rescanMods() to force rescanning.
    """
    roots, cacheKey = _normalizeRoots(overrideRoots)
    
    with _SCAN_LOCK:
        cached = _SCAN_CACHE.get(cacheKey)
        if cached is not None:
            return cached
    
    found: dict[str, tuple[Path, Path, ModManifest, str]] = {}
    allowSymlinks = configBool("mods.allowSymlinks", False)

    for root in roots:
        try:
            if not root.exists():
                continue
        except Exception:
            continue
        _scanDir(root, root, found, allowSymlinks=allowSymlinks)

    logger.info(
        "Mods discovered: %d (allowSymlinks=%s)",
        len(found),
        allowSymlinks
    )
    
    with _SCAN_LOCK:
        _SCAN_CACHE[cacheKey] = found
    return found



def rescanMods(overrideRoots: Iterable[Path] | None = None) -> dict[str, tuple[Path, Path, ModManifest, str]]:
    """
    Clears the cache for the given root-set (or the default set) and rescans.
    """
    _roots, cacheKey = _normalizeRoots(overrideRoots)
    with _SCAN_LOCK:
        _SCAN_CACHE.pop(cacheKey, None)
    return scanMods(overrideRoots)



def findManifestPath(dir: Path) -> Path | None:
    p1, p2 = dir / "mod.json5", dir / "mod.json"
    if p1.exists() and p1.is_file(): return p1
    if p2.exists() and p2.is_file(): return p2
    return None



def scanModsForMount(mountId: str) -> dict[str, tuple[Path, Path, ModManifest, str]]:
    roots = getRegisteredRoots(mountId)
    if not roots:
        return {}
    return scanMods(roots)



def rescanModsForMount(mountId: str) -> dict[str, tuple[Path, Path, ModManifest, str]]:
    roots = getRegisteredRoots(mountId)
    if not roots:
        return {}
    return rescanMods(roots)
