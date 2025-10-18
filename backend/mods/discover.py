# backend/mods/discover.py
from __future__ import annotations
import os, json5
from typing import Any, Mapping # pyright: ignore[reportShadowedImports]
from pathlib import Path
from functools import lru_cache

from backend.app.settings import settings_bool
from backend.mods.manifest import ModManifest
from backend.app.paths import ROOT_DIR

import logging
logger = logging.getLogger(__name__)



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



MOD_ROOTS = defaultModRoots()



def _scanDir(root: Path, parent: Path, out: dict[str, tuple[Path, Path, ModManifest, str]]) -> None:
    """
    Recursively discover mods under `root`, honoring the symlink policy:
    - If allowSymlinks == False:
      • Do NOT descend into symlink directories
      • Do NOT accept manifests that are symlinks
    - Always ensure discovered directories remain within root
    """
    allowSymlinks = settings_bool("mods.allowSymlinks", False)

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
            raw = json5.loads(manifestPath.read_text())
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
            if not ch.is_dir():
                continue
            if not allowSymlinks and ch.is_symlink():
                continue
        except Exception:
            # If any error occurs for this entry, just skip it
            continue

        _scanDir(root, ch, out)



@lru_cache(maxsize=1)
def scanMods() -> dict[str, tuple[Path, Path, ModManifest, str]]:
    """
    Returns a mapping:
      modId -> (root, moddir, manifest, manifestFileName)
    Results are cached. Use rescanMods() to force rescanning.
    """
    found: dict[str, tuple[Path, Path, ModManifest, str]] = {}
    for root, _cfg in MOD_ROOTS:
        try:
            if not root.exists():
                continue
        except Exception:
            continue
        _scanDir(root, root, found)

    logger.info(
        "Mods discovered (cached): %d (allowSymlinks=%s)",
        len(found),
        settings_bool("mods.allowSymlinks", False)
    )
    return found



def rescanMods() -> dict[str, tuple[Path, Path, ModManifest, str]]:
    """Clears the scan cache and returns a fresh scan result."""
    scanMods.cache_clear()
    return scanMods()



def findManifestPath(dir: Path) -> Path | None:
    p1, p2 = dir / "mod.json5", dir / "mod.json"
    if p1.exists() and p1.is_file(): return p1
    if p2.exists() and p2.is_file(): return p2
    return None
