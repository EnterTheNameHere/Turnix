# file: backend/io/managedIo.py ; version: 4
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from backend.core.errors import ActantError
from backend.core.runtimeIds import newRuntimeId

__all__ = [
    "IoDecodeError",
    "IoEncodeError",
    "IoError",
    "IoNotFoundError",
    "IoPathError",
    "IoPermissionError",
    "IoWriteError",
    "ManagedIo",
]


class IoError(ActantError, RuntimeError):
    """Base error for declared Actant-mediated file-operation failures."""


class IoPathError(IoError):
    """Raised when an I/O path cannot be resolved or is not usable as requested."""


class IoNotFoundError(IoError):
    """Raised when an explicitly requested file does not exist."""


class IoPermissionError(IoError):
    """Raised when the operating system denies an Actant-mediated file operation."""


class IoDecodeError(IoError):
    """Raised when file bytes cannot be decoded or parsed as requested."""


class IoEncodeError(IoError):
    """Raised when a requested structured value cannot be encoded."""


class IoWriteError(IoError):
    """Raised when a mediated write cannot be completed atomically."""


class ManagedIo:
    """Central Actant file-I/O service used by Pack-facing Context facades.

    Language-facing Pack code receives only Context facades around this object.
    All filesystem exceptions are translated here so Pack implementations do
    not need language-specific error handling for ordinary supported I/O.
    """

    def readText(self, path: str | Path) -> str:
        resolved = self._path(path)
        try:
            return resolved.read_text(encoding="utf-8")
        except FileNotFoundError as err:
            raise IoNotFoundError(f"File does not exist: {resolved}.") from err
        except PermissionError as err:
            raise IoPermissionError(f"Permission denied while reading {resolved}.") from err
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
        temporary = resolved.with_name(f".{resolved.name}.{newRuntimeId()}.tmp")
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(resolved)
        except PermissionError as err:
            raise IoPermissionError(f"Permission denied while writing {resolved}.") from err
        except OSError as err:
            raise IoWriteError(f"Failed to atomically write {resolved}: {err}.") from err
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    def writeJsonAtomic(self, path: str | Path, value: object) -> None:
        resolved = self._path(path)
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        except (TypeError, ValueError) as err:
            raise IoEncodeError(f"Value cannot be encoded as JSON for {resolved}: {err}.") from err
        self.writeTextAtomic(resolved, text)

    @staticmethod
    def _path(path: str | Path) -> Path:
        if isinstance(path, Path):
            return path.expanduser().resolve()
        if type(path) is not str or not path:
            raise IoPathError("I/O path must be a non-empty string or pathlib.Path.")
        try:
            return Path(path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as err:
            raise IoPathError(f"Invalid I/O path {path!r}: {err}.") from err
