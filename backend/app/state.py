# backend/app/state.py
from __future__ import annotations
from typing import Any, TYPE_CHECKING

from backend.core.permissions import PermissionManager

if TYPE_CHECKING:
    from backend.app.shell import AppShell
    from backend.game.realm import GameRealm
    from backend.mods.loader import LoadedPyMod

SERVICES: dict[str, Any] = {}
PYMODS_LOADED: list["LoadedPyMod"] = []
PYMODS_FAILED: list[dict] = []
PERMS = PermissionManager()

# Process wide pointers to the main menu (AppShell) and the currently running game (GameRealm).
# Exactly one AppShell exists; at most one GameRealm exists at a time.
APP_SHELL: "AppShell | None" = None
GAME_REALM: "GameRealm | None" = None
