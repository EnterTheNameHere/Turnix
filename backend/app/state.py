# backend/app/state.py
from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.mods.loader import LoadedPyMod

SERVICES: dict[str, Any] = {}
PYMODS_LOADED: list[LoadedPyMod] = []
PYMODS_FAILED: list[dict] = []
