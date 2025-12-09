# backend/core/hashing.py
from __future__ import annotations

import hashlib
from pathlib import Path



def sha256sumWithPath(path: str | Path) -> str:
    """Returns a SHA-256 hex digest of the file content combined with its absolute path."""
    path = Path(path).resolve()
    sha = hashlib.sha256()

    # Include absolute path in the hash
    sha.update(str(path).encode("utf-8"))

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            sha.update(chunk)
    
    return sha.hexdigest()
