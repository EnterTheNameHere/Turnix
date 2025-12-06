# backend/config/providers.py
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any, cast
from collections.abc import Mapping
from pathlib import Path
import logging

import json5

from backend.core.dictpath import getByPath, setByPath, deleteByPath
from backend.core.utils import deepCopy
from .types import ConfigProvider, ConfigStore

logger = logging.getLogger(__name__)

__all__ = [
    "OverrideProvider", "DictProvider", "DefaultsProvider",
    "FileProvider", "ViewProvider"
]

# ----------------------------------------------
#          OverrideProvider (in-memory)
# ----------------------------------------------

class OverrideProvider(ConfigProvider):
    """
    Volatile, writable, topmost override layer (never saved to disk).
    """
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
    
    def get(self, key: str) -> Any | None:
        value = getByPath(self._data, key, None)
        return value
    
    def set(self, key: str, value: Any) -> None:
        if value is None:
            deleteByPath(self._data, key, pruneEmptyParents=True)
            return
        
        setByPath(self._data, key, deepCopy(value), createIfMissing=True)
    
    def to_dict(self) -> dict[str, Any]:
        return deepCopy(self._data)
    
    def save(self) -> None:
        return # Nothing to do


# ----------------------------------------------
#       Read-only dict (shipped defaults)
# ----------------------------------------------

@dataclass
class DictProvider(ConfigProvider):
    """
    Read-only mapping: (e.g., embedded defaults loaded at boot).
    """
    data: Mapping[str, Any]

    def get(self, key: str) -> Any | None:
        value = getByPath(self.data, key, None)
        return value

    def set(self, key: str, value: Any) -> None:
        raise RuntimeError("DictProvider is read-only")
    
    def to_dict(self) -> dict[str, Any]:
        return deepCopy(dict(self.data))
    
    def save(self) -> None:
        return



class DefaultsProvider(ConfigProvider):
    """
    Read-only provider for shipped default configuration.
    
    Can be initialized either from a JSON/JSON5 file (via `path`)
    or from an in-memory mapping (via `data`).

    If strict=True (default), a missing file raises FileNotFoundError.
    If strict=False, a missing file results in an empty mapping.

    Example:
        DefaultsProvider(path="./assets/config/defaults/settings_default.json5")
        DefaultsProvider(data={"protocol": {"ackWaitMs": 250}})
    
    Raises:
        ValueError: if neither `data` nor `path` is provided
        FileNotFoundError: if file is missing and strict=True
        TypeError: if loaded `data` is not a Mapping
    """
    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        path: Path | str | None = None,
        strict: bool = True
    ) -> None:
        if data is not None and path is not None:
            raise ValueError(f"{type(self).__name__}: provide either 'data' or 'path', not both")

        if path is not None:
            path = Path(path)
            
            if not path.exists():
                if strict:
                    raise FileNotFoundError(f"{type(self).__name__}: defaults file '{path}' not found")
                # strict=False → use empty data and bail out immediately
                self.data = {}
                return
        
            if not path.is_file():
                raise FileNotFoundError(f"{type(self).__name__}: '{str(path)}' is not a file")
            
            # File exists by here → parse normally
            try:
                parsed = json5.loads(path.read_text("utf-8"))
            except Exception as err:
                raise TypeError(f"{type(self).__name__}: failed to parse '{path}': {err}") from err

            if not isinstance(parsed, Mapping):
                raise TypeError(
                    f"{type(self).__name__}: file content must be a JSON object, not '{type(parsed).__name__}'"
                )
            
            self.data = cast(Mapping[str, Any], parsed)
        
        elif data is not None:
            if not isinstance(data, Mapping):
                raise TypeError(f"{type(self).__name__}: 'data' must be a Mapping, not '{type(data).__name__}'")
            self.data = data

        else:
            raise ValueError(f"{type(self).__name__}: either 'data' or 'path' must be provided")
        
    def get(self, key: str) -> Any | None:
        value = getByPath(self.data, key, None)
        return value
    
    def set(self, key: str, value: Any) -> None:
        raise RuntimeError(f"{type(self).__name__}: is read-only")
    
    def to_dict(self) -> Mapping[str, Any]:
        # Always return a deep copy to prevent accidental mutation
        return deepCopy(self.data, strict=False)

    def save(self) -> None:
        # read-only, no save()
        pass



# ----------------------------------------------
#        File-backed provider JSON/JSON5
# ----------------------------------------------

class FileProvider(ConfigProvider):
    """
    Writable configuration provider that persists to a .json or .json5 file.
    
    Used for user or save-specific overrides layered above defaults.
    If readOnly=True, set() and save() will raise.

    Example:
        fp = FileProvider("./userdata/config/global.json5")
        fp.set("debug.tracingEnabled", True)
        fp.save()
    
    Behavior:
        • Missing file → starts with empty dict
        • Parse error → logs warning and starts empty dict
        • Non-object JSON → raises TypeError
    """
    def __init__(self, path: str | Path, *, readOnly: bool = False) -> None:
        self.path = Path(path)
        self.readOnly = readOnly
        self._data: dict[str, Any] = {}
        self._loaded = False
        self._load()
    
    def _load(self) -> None:
        self._data.clear()
        self._loaded = True
        
        if not self.path.exists():
            logger.debug("%s: '%s' is missing → starting as empty dict", type(self).__name__, self.path)
            return
        
        if not self.path.is_file():
            raise IsADirectoryError(f"{type(self).__name__}: '{self.path}' exists but is not a file")

        try:
            text = self.path.read_text(encoding="utf-8")
        except Exception as err:
            logger.error("%s: failed to read '%s': %s", type(self).__name__, self.path, err)
            return
        
        try:
            parsed = json5.loads(text)
        except Exception as err:
            logger.warning("%s: parse failed for '%s': %s", type(self).__name__, self.path, err)
            logger.debug("%s: starting as empty dict", type(self).__name__)
            parsed = {}
        
        if parsed is None:
            logger.debug("%s: parsed is None, starting as empty dict", type(self).__name__)
            parsed = {}

        if not isinstance(parsed, Mapping):
            raise TypeError(f"{type(self).__name__}: file content must be a JSON object, not '{type(parsed).__name__}'")
        
        self._data = dict(parsed)

    def get(self, key: str) -> Any | None:
        value = getByPath(self._data, key, None)
        return value

    def set(self, key: str, value: Any) -> None:
        if self.readOnly:
            raise RuntimeError(f"{type(self).__name__}({self.path}) is read-only")
        
        if value is None:
            deleteByPath(self._data, key, pruneEmptyParents=True)
            return
        
        setByPath(self._data, key, deepCopy(value, strict=False), createIfMissing=True)

    def to_dict(self) -> dict[str, Any]:
        return deepCopy(self._data, strict=False)

    def save(self) -> None:
        if self.readOnly:
            raise RuntimeError(f"{type(self).__name__}({self.path}) is read-only")
        
        # Ensure parent directory exists
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as err:
            raise OSError(f"{type(self).__name__}: cannot create parent directory '{self.path.parent}': {err}") from err
        
        # Serialize as JSON5
        try:
            out = json5.dumps(self._data, indent=2, quote_keys=True)
        except Exception as err:
            raise TypeError(f"{type(self).__name__}: failed to serialize data to JSON5: {err}") from err
        
        # Atomic write
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fl:
            fl.write(out)
            if not out.endswith("\n"):
                fl.write("\n")
        
        os.replace(tmp_path, self.path)
        logger.debug("%s: saved %d keys to '%s'", type(self).__name__, len(self._data), self.path)


# ----------------------------------------------
# View provider (read-through from another store)
# ----------------------------------------------

class ViewProvider(ConfigProvider):
    """
    Read-only window into another ConfigStore (e.g., global from a realm)
    """
    def __init__(self, store: "ConfigStore") -> None:
        self._store = store
    
    def get(self, key: str) -> Any | None:
        return self._store.get(key)
    
    def set(self, key: str, value: Any) -> None:
        raise RuntimeError("ViewProvider is read-only")

    def to_dict(self) -> dict[str, Any]:
        return deepCopy(self._store.snapshot()["values"])
    
    def save(self) -> None:
        return
