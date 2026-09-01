# file: backend/io/managedIo.py ; version: 2
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from backend.core.runtimeIds import newRuntimeId

__all__ = ["IoError", "IoNotFoundError", "IoDecodeError", "IoWriteError", "ManagedIo"]


class IoError(RuntimeError):
    """Base error for Actant-mediated file operations."""


class IoNotFoundError(IoError):
    """Raised when an explicitly requested file does not exist."""


class IoDecodeError(IoError):
    """Raised when file bytes cannot be decoded or parsed as requested."""


class IoWriteError(IoError):
    """Raised when a mediated write cannot be completed atomically."""


class ManagedIo:
    """Central Actant file-I/O service used by Pack-facing Context facades."""

    def readText(self, path: str | Path) -> str:
        resolved = self._path(path)
        try:
            return resolved.read_text(encoding="utf-8")
        except FileNotFoundError as err:
            raise IoNotFoundError(f"File does not exist: {resolved}.") from err
        except UnicodeDecodeError as err:
            raise IoDecodeError(f"File is not valid UTF-8: {resolved}.") from err
        except OSError as err:
            raise IoError(f"Failed to read file {resolved}: {err}.") from err

    def readJson(self, path: str | Path) -> dict[str, Any]:
        resolved = self._path(path)
        try:
            value = json.loads(self.readText(resolved))
        except json.JSONDecodeError as err:
            raise IoDecodeError(f"Invalid JSON in {resolved}: {err}.") from err
        if not isinstance(value, dict):
            raise IoDecodeError(f"JSON root must be an object: {resolved}.")
        return value

    def readLines(self, path: str | Path) -> tuple[str, ...]:
        return tuple(self.readText(path).splitlines())

    def writeTextAtomic(self, path: str | Path, text: str) -> None:
        if type(text) is not str:
            raise TypeError("text must be an exact built-in string.")
        resolved = self._path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        temporary = resolved.with_name(f".{resolved.name}.{newRuntimeId()}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8", newline="\n")
            temporary.replace(resolved)
        except OSError as err:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise IoWriteError(f"Failed atomic write to {resolved}: {err}.") from err

    def writeJsonAtomic(self, path: str | Path, value: object) -> None:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        except (TypeError, ValueError) as err:
            raise IoWriteError(f"Value is not JSON serializable: {err}.") from err
        self.writeTextAtomic(path, text)

    @staticmethod
    def _path(path: str | Path) -> Path:
        if isinstance(path, Path):
            return path.expanduser().resolve()
        if type(path) is str and path:
            return Path(path).expanduser().resolve()
        raise TypeError("path must be a pathlib.Path or non-empty exact built-in string.")
