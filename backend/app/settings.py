# backend/app/settings.py
from __future__ import annotations
import json5, os
from pydantic import JsonValue
from pathlib import Path
from typing import Any, cast
from functools import lru_cache

from backend.app.paths import BACKEND_DIR
from backend.core.dictpath import getByPath

import logging
logger = logging.getLogger(__name__)

__all__ = [
    "SETTINGS_DEFAULT_PATH", "SETTINGS", "loadUserSettings",
    "loadSettings", "deepMerge", "allowSymlinks", "pickBudgetMs",
    "resolveClassCfg"
]


SETTINGS_DEFAULT_PATH = BACKEND_DIR / "settings_default.json5"
SETTINGS: JsonValue = (
    json5.loads(SETTINGS_DEFAULT_PATH.read_text())
    if SETTINGS_DEFAULT_PATH.exists()
    else {
        "__source": "BACKEND_DEFAULTS",
        "protocol": {"ackWaitMs": 250, "graceWindowMs": 150, "maxInFlightPerLane": 64, "heartbeatMs": 5000, "maxQueue": 1024, "maxOfflineQueue": 2000},
        "reconnect": {"initialDelayMs": 500, "maxDelayMs": 15000, "factor": 2.0, "jitterRatio": 0.25},
        "timeouts": {"classes": {
            "request.fast":   {"serviceTtlMs": 800,  "clientPatienceExtraMs": 150},
            "request.medium": {"serviceTtlMs": 3000, "clientPatienceExtraMs": 200},
            "request.heavy":  {"serviceTtlMs": 30000,"clientPatienceExtraMs": 250}}
        },
        "streams": {"default": {"targetHz": 10, "maxQueueMs": 200, "coalesce": "drop-oldest"}},
        "http": {"retry": 2, "backoff": {"baseMs": 250, "maxMs": 1000, "jitterPct": 30}, "timeoutCapMs": 30000},
        "mods": {"allowSymlinks": False},
        "httpProxy": {
            "allowList": ["httpbin.org", "api.openai.com", "localhost", "127.0.0.1", "::1"],
            "buckets": {"default": {"rpm": 600, "burst": 200}},
        },
        "debug": {"backend":  {"rpc": {"maxPreviewChars": 1_000_000,
                                    "incomingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"]},
                                    "outgoingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"], "rules": [{"type": "stateUpdate", "shouldLog": True, "tests": [{"property": "payload.done", "op": "notExists", "value": True, "shouldLog": False}]}]}}},
                  "frontend": {"rpc": {"maxPreviewChars": 1_000_000,
                                    "incomingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"], "rules": [{"type": "stateUpdate", "shouldLog": True, "tests": [{"property": "payload.done", "op": "notExists", "value": True, "shouldLog": False}]}]},
                                    "outgoingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"]}}}
        },
    }
)



def loadUserSettings() -> JsonValue:
    filePath = Path(os.path.expanduser("~/.turnix/turnix.json5"))
    if filePath.exists():
        try:
            return json5.loads(filePath.read_text())
        except Exception as err:
            logger.error("Failed to parse '%s': %s", filePath, err)
    return {}



@lru_cache(maxsize=1)
def loadSettings():
    return deepMerge(SETTINGS, loadUserSettings())



def deepMerge(first: JsonValue, second: JsonValue) -> JsonValue:
    """
    Returns a new JsonValue where keys from `second` override/extend `first`.
    Only merges recursively when BOTH sides are JSON objects (dicts).
    For all other JSON types (lists, strings, numbers, booleans, null),
    the right-hand value `second` replaces `first`.
    """
    if isinstance(first, dict) and isinstance(second, dict):
        out: dict[str, JsonValue] = {}
        # Start with left
        for key, value in first.items():
            out[key] = cast(JsonValue, value)
        # Overlay right
        for key, value in second.items():
            if key in out:
                out[key] = deepMerge(out[key], cast(JsonValue, value))
            else:
                out[key] = cast(JsonValue, value)
        return cast(JsonValue, out)
    
    # If not both dicts, replace with right-hand side
    return cast(JsonValue, second)

# ---------- Ergonomic accessors over merged settings ----------

def settings(path: str, default: Any = None) -> Any:
    """Returns value at `path` from merged settings, or `default` if missing."""
    val = getByPath(loadSettings(), path)
    return default if val is None else val



def settings_bool(path: str, default: bool = False) -> bool:
    """Returns bool value at `path` or `default` if missing."""
    val = getByPath(loadSettings(), path)
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return bool(val)

# --------------------------------------------------------------

def allowSymlinks() -> bool:
    return settings_bool("mods.allowSymlinks", False)



def pickBudgetMs(opts) -> int:
    if isinstance(opts, dict):
        budgetMs = opts.get("budgetMs")
        if budgetMs is None:
            return int(resolveClassCfg(opts).get("serviceTtlMs", 3000))
        return int(budgetMs)
    return 3000



def resolveClassCfg(opts) -> dict:
    cls = opts.get("class") or "request.medium"
    classes = settings("timeouts.classes", {})
    cfg = classes.get(cls) if isinstance(classes, dict) else None
    return cfg or {"serviceTtlMs": 3000, "clientPatienceExtraMs": 200}
