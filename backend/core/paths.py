# backend/core/paths.py
from __future__ import annotations
from pathlib import Path
from os import PathLike
from fastapi import HTTPException
from backend.app.config import configBool



def resolveSafe(root: Path, requested: str | PathLike[str] | None) -> Path:
    """
    Returns a path under `root` for `requested`, rejecting traversal and (optionally) symlinks.
    Raises HTTPException(403) if the path leaves root or violates the symlink policy.
    """
    if not isinstance(root, Path):
        root = Path(root)
    
    requested = requested or "."
    raw = root.joinpath(requested)
    
    resolved = raw.resolve(strict=False) # Don't raise if file doesn't exist yet
    rootResolved = root.resolve(strict=True) # Raises if root doesn't exist
    
    # Must remain inside the mod root
    if not resolved.is_relative_to(rootResolved):
        raise HTTPException(403, "Mod path points outside of mod root directory")

    allow_symlinks = configBool("mods.allowSymlinks", False)
    if not allow_symlinks:
        # Leaf itself must not be a symlink either
        if resolved.is_symlink():
            raise HTTPException(403, "Mod file symlink not allowed")
        
        # If caller is the root itself("", ".", or "/"), we're done.
        if resolved == rootResolved:
            return resolved
        
        # Parent chain must not include symlinks
        path = raw
        while True:
            if path.is_symlink():
                raise HTTPException(403, "Mod path symlinks not allowed")
            
            if path.resolve(strict=False) == rootResolved:
                break

            parent = path.parent
            if parent == path: # Filesystem root guard
                break
            path = parent
    return resolved
