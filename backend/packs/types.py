# backend/packs/types.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["ResolvedPack"]



@dataclass(frozen=True, slots=True)
class ResolvedPack:
    """
    Resolved pack metadata the engine can pass around without re-discovery.
    """
    id: str                     # Canonical identifier, e.g. "Turnix@main_menu" or "aiChat"
    name: str                   # Human-friendly name
    version: str                # Semver
    root: Path                  # root category folder (e.g. assets/appPacks)
    dir: Path                   # actual pack directory
    manifestPath: Path          # manifest's path
    manifest: dict[str, Any]    # parsed manifest
