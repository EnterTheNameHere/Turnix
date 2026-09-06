# file: backend/io/managedIo.py ; version: 5
from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass
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
    "SourceObservation",
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


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """One immutable observation of a filesystem source.

    SourceObservation is evidence about the source at one observation point,
    not an automatically maintained subscription. The representation is
    deliberately suitable for persistence inside Actant-managed values so a
    producer can later compare the basis of a derived result with a fresh
    observation.

    Metadata-only observations are cheap and suitable for ordinary polling.
    contentSha256 is present only when strong content identity was requested.
    A future watcher/event service can reuse this same representation when it
    reports source transitions; Pack-facing source identity therefore does not
    need to change when Actant gains continual observation.

    This contract is design-significant and should be promoted into the I/O /
    source-observation design specification when design documents are updated.
    """

    path: str
    state: str
    sizeBytes: int | None
    modifiedTimeNs: int | None
    contentSha256: str | None

    def snapshot(self) -> dict[str, object]:
        """Returns the JSON-compatible observation representation."""
        return {
            "path": self.path,
            "state": self.state,
            "sizeBytes": self.sizeBytes,
            "modifiedTimeNs": self.modifiedTimeNs,
            "contentSha256": self.contentSha256,
        }


class ManagedIo:
    """Central Actant file-I/O service used by Pack-facing Context facades.

    Language-facing Pack code receives only Context facades around this object.
    All filesystem exceptions are translated here so Pack implementations do
    not need language-specific error handling for ordinary supported I/O.
    """

    def observeFile(
        self,
        path: str | Path,
        *,
        contentHash: bool = False,
    ) -> SourceObservation:
        """Observes current filesystem state for one source path.

        Missing paths are represented rather than raised so creation and
        deletion are ordinary observable transitions. Existing non-file paths
        are represented as state="other"; requesting a content hash for such a
        path fails because byte-content identity is defined here only for files.

        Metadata-only observation records size and nanosecond modification
        time. These fields are efficient change indicators but are not a proof
        of byte equality. Callers whose authority decision requires strong
        source identity request contentHash=True and persist contentSha256 as
        part of their derivation provenance.

        This method performs one observation only. Scheduling, polling,
        subscriptions, and source-change event publication belong to future
        Actant services layered over this primitive.
        """
        if type(contentHash) is not bool:
            raise TypeError("contentHash must be an exact bool.")

        resolved = self._path(path)
        try:
            stat = resolved.stat()
        except FileNotFoundError:
            return SourceObservation(
                path=str(resolved),
                state="missing",
                sizeBytes=None,
                modifiedTimeNs=None,
                contentSha256=None,
            )
        except PermissionError as err:
            raise IoPermissionError(f"Permission denied while observing {resolved}.") from err
        except OSError as err:
            raise IoError(f"Failed to observe file {resolved}: {err}.") from err

        if not resolved.is_file():
            if contentHash:
                raise IoPathError(f"Content hashing requires a regular file: {resolved}.")
            return SourceObservation(
                path=str(resolved),
                state="other",
                sizeBytes=None,
                modifiedTimeNs=stat.st_mtime_ns,
                contentSha256=None,
            )

        digest: str | None = None
        if contentHash:
            hasher = hashlib.sha256()
            try:
                with resolved.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        hasher.update(chunk)
            except FileNotFoundError:
                # The path changed between stat() and open(). Represent the
                # observation point conservatively as missing rather than
                # returning metadata for bytes we could not prove.
                return SourceObservation(
                    path=str(resolved),
                    state="missing",
                    sizeBytes=None,
                    modifiedTimeNs=None,
                    contentSha256=None,
                )
            except PermissionError as err:
                raise IoPermissionError(f"Permission denied while hashing {resolved}.") from err
            except OSError as err:
                raise IoError(f"Failed to hash file {resolved}: {err}.") from err
            digest = hasher.hexdigest()

        return SourceObservation(
            path=str(resolved),
            state="file",
            sizeBytes=stat.st_size,
            modifiedTimeNs=stat.st_mtime_ns,
            contentSha256=digest,
        )

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
