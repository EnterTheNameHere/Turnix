# file: backend/save/applicationStore.py ; version: 5
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.save.runtime import SaveBundle

__all__ = ["ApplicationStore", "LoadedApplicationSave"]


@dataclass(frozen=True, slots=True)
class LoadedApplicationSave:
    """Validated durable Application storage resolved by Application identity."""

    appPackId: str
    applicationId: str
    bundle: SaveBundle
    recoveredFromGeneration: int | None = None


class ApplicationStore:
    """Filesystem authority for durable Application SaveBundle generations.

    Application state lives below:
        <savesRoot>/<appPackId>/<applicationId>/

    SaveBundle generation files are immutable. Publication writes and validates
    a complete new generation before atomically replacing the small current
    pointer. Existing accepted generations are never modified in place.

    The store serializes only SaveBundle state, which is already a snapshot of
    the authoritative persistent-memory root. Open transaction layers are not
    visible to this boundary and therefore cannot become durable Application
    state accidentally.
    """

    _APPLICATION_FORMAT = "actant.application@1"
    _CURRENT_FORMAT = "actant.application-current@1"

    def __init__(self, savesRoot: str | os.PathLike[str]) -> None:
        root = Path(savesRoot)
        self._root = root.resolve()

    @property
    def savesRoot(self) -> Path:
        return self._root

    @staticmethod
    def _identitySegment(value: str, *, fieldName: str) -> str:
        if type(value) is not str or not value:
            raise ValueError(f"{fieldName} must be a non-empty string.")
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError(f"{fieldName} must be one filesystem path segment.")
        return value

    def applicationPath(self, *, appPackId: str, applicationId: str) -> Path:
        appPack = self._identitySegment(appPackId, fieldName="appPackId")
        application = self._identitySegment(applicationId, fieldName="applicationId")
        return self._root / appPack / application

    @staticmethod
    def _canonicalJsonBytes(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _writeFileDurably(path: Path, payload: bytes) -> None:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _replaceFileDurably(path: Path, payload: bytes) -> None:
        descriptor, temporaryName = tempfile.mkstemp(
            prefix=f".{path.name}.tmp-",
            dir=path.parent,
        )
        temporary = Path(temporaryName)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _generationName(generation: int) -> str:
        if type(generation) is not int or generation <= 0:
            raise ValueError("generation must be a positive exact integer.")
        return f"{generation:08d}.bundle"

    @staticmethod
    def _bundleSha256(bundle: SaveBundle) -> str:
        return hashlib.sha256(bundle.toBytes()).hexdigest()

    @classmethod
    def _availableGenerations(
        cls,
        generationsDirectory: Path,
        *,
        atOrBelow: int,
    ) -> tuple[int, ...]:
        try:
            entries = tuple(generationsDirectory.iterdir())
        except OSError as err:
            raise ValueError("Application generations directory is missing or unreadable.") from err

        generations: set[int] = set()
        for entry in entries:
            if not entry.is_file() or not entry.name.endswith(".bundle"):
                continue
            stem = entry.name[:-len(".bundle")]
            if not stem.isdecimal():
                continue
            generation = int(stem)
            if generation <= 0 or generation > atOrBelow:
                continue
            if cls._generationName(generation) != entry.name:
                continue
            generations.add(generation)
        return tuple(sorted(generations, reverse=True))


    def _applicationMetadata(self, bundle: SaveBundle) -> dict[str, object]:
        return {
            "formatId": self._APPLICATION_FORMAT,
            "appPackId": bundle.appPackId,
            "applicationId": bundle.applicationId,
            "saveBundleId": bundle.saveBundleId,
            "createdAt": datetime.now(UTC).isoformat(),
        }

    def _currentPointer(self, bundle: SaveBundle) -> dict[str, object]:
        return {
            "formatId": self._CURRENT_FORMAT,
            "saveBundleId": bundle.saveBundleId,
            "generation": bundle.generation,
            "sha256": self._bundleSha256(bundle),
        }

    def _validateCurrentPointer(
        self,
        current: dict[str, object],
        *,
        saveBundleId: str,
    ) -> int:
        pointerSaveBundleId = current.get("saveBundleId")
        if pointerSaveBundleId != saveBundleId:
            raise ValueError("Application current pointer SaveBundle identity does not match Application metadata.")
        generation = current.get("generation")
        if type(generation) is not int or generation <= 0:
            raise ValueError("Application current pointer requires positive generation.")
        sha256 = current.get("sha256")
        if type(sha256) is not str or len(sha256) != 64:
            raise ValueError("Application current pointer requires a SHA-256 digest.")
        try:
            int(sha256, 16)
        except ValueError as err:
            raise ValueError("Application current pointer requires a SHA-256 digest.") from err
        return generation

    def _validateBundleIdentity(
        self,
        bundle: SaveBundle,
        *,
        appPackId: str,
        applicationId: str,
        saveBundleId: str | None = None,
    ) -> None:
        if bundle.appPackId != appPackId:
            raise ValueError("SaveBundle appPackId does not match Application storage identity.")
        if bundle.applicationId != applicationId:
            raise ValueError("SaveBundle applicationId does not match Application storage identity.")
        if saveBundleId is not None and bundle.saveBundleId != saveBundleId:
            raise ValueError("SaveBundle identity does not match existing Application storage.")

    def createApplication(self, bundle: SaveBundle) -> Path:
        """Atomically publishes generation 1 for one previously nonexistent Application."""
        if not isinstance(bundle, SaveBundle):
            raise TypeError("bundle must be a SaveBundle.")
        if bundle.generation != 1:
            raise ValueError("Application creation requires SaveBundle generation 1.")

        target = self.applicationPath(
            appPackId=bundle.appPackId,
            applicationId=bundle.applicationId,
        )
        if target.exists():
            raise FileExistsError(f"Application storage already exists: {target}")

        appPackDirectory = target.parent
        appPackDirectory.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".creating-{bundle.applicationId}-",
                dir=appPackDirectory,
            ),
        )
        generations = staging / "generations"
        generations.mkdir()

        try:
            self._writeFileDurably(
                staging / "application.json",
                self._canonicalJsonBytes(self._applicationMetadata(bundle)),
            )
            generationPath = generations / self._generationName(bundle.generation)
            self._writeFileDurably(generationPath, bundle.toBytes())

            validated = SaveBundle.fromBytes(generationPath.read_bytes())
            self._validateBundleIdentity(
                validated,
                appPackId=bundle.appPackId,
                applicationId=bundle.applicationId,
                saveBundleId=bundle.saveBundleId,
            )
            if validated.generation != 1:
                raise RuntimeError("Published initial SaveBundle changed generation during validation.")

            self._writeFileDurably(
                staging / "current",
                self._canonicalJsonBytes(self._currentPointer(bundle)),
            )
            os.replace(staging, target)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

        return target

    def publish(self, bundle: SaveBundle) -> Path:
        """Publishes one immutable next generation and atomically accepts it."""
        if not isinstance(bundle, SaveBundle):
            raise TypeError("bundle must be a SaveBundle.")

        applicationPath = self.applicationPath(
            appPackId=bundle.appPackId,
            applicationId=bundle.applicationId,
        )
        metadata = self._readApplicationMetadata(applicationPath)
        saveBundleId = metadata["saveBundleId"]
        if type(saveBundleId) is not str:
            raise RuntimeError("Application metadata saveBundleId is invalid.")
        self._validateBundleIdentity(
            bundle,
            appPackId=bundle.appPackId,
            applicationId=bundle.applicationId,
            saveBundleId=saveBundleId,
        )

        current = self._readCurrent(applicationPath)
        currentGeneration = self._validateCurrentPointer(
            current,
            saveBundleId=saveBundleId,
        )
        if bundle.generation != currentGeneration + 1:
            raise ValueError(
                "SaveBundle publication must advance exactly one generation "
                f"from {currentGeneration} to {currentGeneration + 1}.",
            )

        generationPath = applicationPath / "generations" / self._generationName(bundle.generation)
        if generationPath.exists():
            raise FileExistsError(f"SaveBundle generation already exists: {generationPath}")
        descriptor, temporaryName = tempfile.mkstemp(
            prefix=f".{generationPath.name}.tmp-",
            dir=generationPath.parent,
        )
        temporary = Path(temporaryName)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(bundle.toBytes())
                stream.flush()
                os.fsync(stream.fileno())
            validated = SaveBundle.fromBytes(temporary.read_bytes())
            self._validateBundleIdentity(
                validated,
                appPackId=bundle.appPackId,
                applicationId=bundle.applicationId,
                saveBundleId=bundle.saveBundleId,
            )
            if validated.generation != bundle.generation:
                raise RuntimeError("SaveBundle generation changed during publication validation.")

            # Generation files are immutable. link() atomically creates the
            # destination only when absent, so a concurrent publisher cannot
            # replace an already-visible generation after our earlier
            # existence check.
            os.link(temporary, generationPath)
            temporary.unlink()

            self._replaceFileDurably(
                applicationPath / "current",
                self._canonicalJsonBytes(self._currentPointer(bundle)),
            )
        finally:
            if temporary.exists():
                temporary.unlink()

        return generationPath

    def load(self, *, appPackId: str, applicationId: str) -> LoadedApplicationSave:
        """Loads the accepted bundle, falling back to the newest valid prior generation."""
        applicationPath = self.applicationPath(
            appPackId=appPackId,
            applicationId=applicationId,
        )
        metadata = self._readApplicationMetadata(applicationPath)
        if metadata.get("appPackId") != appPackId or metadata.get("applicationId") != applicationId:
            raise ValueError("Application metadata identity does not match requested Application.")
        saveBundleId = metadata.get("saveBundleId")
        if type(saveBundleId) is not str or not saveBundleId:
            raise ValueError("Application metadata requires saveBundleId.")

        current = self._readCurrent(applicationPath)
        currentGeneration = self._validateCurrentPointer(
            current,
            saveBundleId=saveBundleId,
        )

        generationsDirectory = applicationPath / "generations"
        candidates = self._availableGenerations(
            generationsDirectory,
            atOrBelow=currentGeneration,
        )
        for generation in candidates:
            generationPath = generationsDirectory / self._generationName(generation)
            try:
                payload = generationPath.read_bytes()
                bundle = SaveBundle.fromBytes(payload)
                self._validateBundleIdentity(
                    bundle,
                    appPackId=appPackId,
                    applicationId=applicationId,
                    saveBundleId=saveBundleId,
                )
                if bundle.generation != generation:
                    raise ValueError("SaveBundle filename generation does not match bundle generation.")
                if generation == currentGeneration:
                    expectedHash = current.get("sha256")
                    actualHash = hashlib.sha256(payload).hexdigest()
                    if type(expectedHash) is not str or actualHash != expectedHash:
                        raise ValueError("Current SaveBundle integrity does not match current pointer.")
                return LoadedApplicationSave(
                    appPackId=appPackId,
                    applicationId=applicationId,
                    bundle=bundle,
                    recoveredFromGeneration=(
                        None if generation == currentGeneration else currentGeneration
                    ),
                )
            except (FileNotFoundError, OSError, TypeError, ValueError):
                continue

        raise RuntimeError(
            f"No valid SaveBundle generation remains for {appPackId}/{applicationId}.",
        )

    def _readApplicationMetadata(self, applicationPath: Path) -> dict[str, object]:
        try:
            value = json.loads((applicationPath / "application.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise ValueError("Application metadata is missing or invalid.") from err
        if not isinstance(value, dict) or value.get("formatId") != self._APPLICATION_FORMAT:
            raise ValueError(f"Application metadata requires formatId {self._APPLICATION_FORMAT!r}.")
        return value

    def _readCurrent(self, applicationPath: Path) -> dict[str, object]:
        try:
            value = json.loads((applicationPath / "current").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise ValueError("Application current pointer is missing or invalid.") from err
        if not isinstance(value, dict) or value.get("formatId") != self._CURRENT_FORMAT:
            raise ValueError(f"Application current pointer requires formatId {self._CURRENT_FORMAT!r}.")
        return value
