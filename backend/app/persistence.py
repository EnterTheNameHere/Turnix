# backend/runtimes/persistence.py
from __future__ import annotations
import logging
import os
import time
from pathlib import Path
from typing import Any

import json5

from backend.app.globals import getTracer
from backend.memory.memory_persistence import saveLayersToDir, loadLayersFromDir
from backend.app.instance import AppInstance
from backend.sessions.session import Session

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Layout under a save directory
# ------------------------------------------------------------------ #
# save.json5                         # top level manifest + file index + metadata
# state/snapshot.json5               # AppInstance snapshot
# state/sessions/<sessionId>.json5   # session snapshot (one per session)
# state/sessions/<sessionId>_layers/ # per-layer files for that session (one file per DictMemoryLayer)
# preview.png                        # optional thumbnail (not indexed/checksummed here)
#
# Notes:
# - We compute SHA256 over state files and store them in the manifest.
# - We write state files first, then the manifest (best-effort atomicity).
# - Migrator exists, but is unused currently
# - Each DictMemoryLayer is saved separately in <sessionId>_layers/.
#

class MissingSnapshotProperty(Exception):
    pass

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def ensureDir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)



def sha256Bytes(data: bytes) -> str:
    import hashlib
    hsh = hashlib.sha256()
    hsh.update(data)
    return hsh.hexdigest()



def writeTextJson5(path: Path, obj: Any) -> str:
    text = json5.dumps(obj, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    return sha256Bytes(text.encode("utf-8"))



def readJson5(path: Path) -> Any:
    return json5.loads(path.read_text(encoding="utf-8"))



def toRelPath(base: Path, path: Path) -> str:
    """
    Returns a POSIX-style relative path string from base to path.
    """
    try:
        rel = os.path.relpath(path, base)
    except Exception:
        rel = str(path)
    return Path(rel).as_posix()


# ------------------------------------------------------------------ #
# Migrator
# ------------------------------------------------------------------ #
    
def migrateIfNeeded(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    """
    Currently no-op.
    """
    return manifest


# ------------------------------------------------------------------ #
# Save
# ------------------------------------------------------------------ #

def save(
    appInstance: AppInstance,
    targetDir: Path | str,
    *,
    label: str | None = None,
    thumbnail: bytes | None = None,
) -> tuple[Path, str]:
    """
    Saves a AppInstance into targetDir.
    Returns (path to 'save.json5', sha256).
    """
    tracer = getTracer()
    span = None
    try:
        tracer.updateTraceContext({
            "appInstanceId": appInstance.id,
            "appPackId": appInstance.appPackId,
        })
        span = tracer.startSpan(
            "appInstance.save",
            attrs={
                "label": label or "",
                "targetDir": str(targetDir),
            },
            tags=["appInstance", "save"],
        )
        tracer.traceEvent(
            "appInstance.save.start",
            level="info",
            tags=["appInstance", "save"],
            span=span,
            attrs={
                "appInstanceId": appInstance.id,
                "appPackId": appInstance.appPackId,
            },
        )
    except Exception:
        span = None
    
    try:
        root = Path(targetDir)
        ensureDir(root)
        
        stateDir = root / "state"
        sessionsDir = stateDir / "sessions"
        ensureDir(stateDir)
        ensureDir(sessionsDir)
        
        filesIndex = {"appInstance": {}, "sessions": {}}
        
        # 1) Write appInstance snapshot
        appInstanceSnapshot = appInstance.snapshot()
        snapshotFilePath = stateDir / "snapshot.json5"
        snapshotHash = writeTextJson5(snapshotFilePath, appInstanceSnapshot)
        filesIndex["appInstance"]["path"] = toRelPath(root, snapshotFilePath)
        filesIndex["appInstance"]["sha256"] = snapshotHash
        
        # 2) Write each session snapshot + per-layer directory
        sessionHashes: dict[str, str] = {}
        for sessionId, session in appInstance.sessionsById.items():
            sessionSnapshot = session.snapshot()
            sessionFilePath = sessionsDir / f"{sessionId}.json5"
            sessionHash = writeTextJson5(sessionFilePath, sessionSnapshot)
            sessionHashes[sessionId] = sessionHash
            filesIndex["sessions"][sessionId] = {}
            filesIndex["sessions"][sessionId]["path"] = toRelPath(root, sessionFilePath)
            filesIndex["sessions"][sessionId]["sha256"] = sessionHash
            
            # Per-layer files
            layersDir = sessionsDir / f"{sessionId}_layers"
            saveLayersToDir(session.memoryLayers, layersDir)
            filesIndex["sessions"][sessionId]["layersDir"] = toRelPath(root, layersDir)
        
        # 3) Optional thumbnail
        if thumbnail is not None:
            (root / "preview.png").write_bytes(thumbnail)
        
        # 4) Compose manifest and write it last
        now = int(time.time())
        manifest = {
            "schemaVersion": "0.0.0", # We will use semver for updates to schemas of save files
            "appPackId": appInstance.appPackId,
            "appInstanceId": appInstance.id,
            "createdTs": int(appInstance.createdTs),
            "savedTs": now,
            "label": label or "",
            "appVersion": "0.0.0", # TODO: Fill when we have build/version system
            "turnixVersion": "0.0.0", # TODO: Fill when we have build/version system
            "files": filesIndex,
            # Convenience for quick restore
            "mainSessionId": appInstance.mainSession.id if appInstance.mainSession else None,
        }
        
        manifestPath = root / "save.json5"
        manifestHash = writeTextJson5(manifestPath, manifest)
        
        logger.info(
            "Saved appInstance '%s' (appPack=%r) to %s (sessions=%d)",
            appInstance.id,
            appInstance.appPackId,
            str(root),
            len(appInstance.sessionsById),
        )
        
        if span is not None:
            try:
                tracer.traceEvent(
                    "appInstance.save.done",
                    tags=["appInstance", "save"],
                    level="info",
                    span=span,
                    attrs={
                        "manifestPath": str(manifestPath),
                        "sessionCount": len(appInstance.sessionsById),
                    },
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["appInstance", "save"],
                    attrs={
                        "savedTs": now,
                    },
                )
            except Exception:
                pass
        
        return (manifestPath, manifestHash)
    
    except Exception as err:
        if span is not None:
            try:
                tracer.traceEvent(
                    "appInstance.save.error",
                    level="error",
                    tags=["appInstance", "save", "error"],
                    span=span,
                    attrs={
                        "errorType": type(err).__name__,
                        "errorMessage": str(err),
                    },
                )
                tracer.endSpan(
                    span,
                    status="error",
                    tags=["appInstance", "save", "error"],
                    errorType=type(err).__name__,
                    errorMessage=str(err),
                )
            except Exception:
                pass
        raise


# ------------------------------------------------------------------ #
# Load
# ------------------------------------------------------------------ #

def loadAppInstance(sourceDir: Path | str) -> AppInstance:
    """
    Loads a AppInstance from sourceDir.
    Validates manifest, performs migrations, reconstructs appInstance + sessions from snapshots,
    and hydrates session memory layers from per-layer directories.
    """
    tracer = getTracer()
    span = None
    rootDir = Path(sourceDir)
    
    try:
        # We do not know appPackId/appInstanceId yet. Set them after manifest loads.
        span = tracer.startSpan(
            "appInstance.load",
            attrs={
                "sourceDir": str(rootDir),
            },
            tags=["appInstance", "load"],
        )
        tracer.traceEvent(
            "appInstance.load.start",
            level="info",
            tags=["appInstance", "load"],
            span=span,
            attrs={
                "manifestPath": str(rootDir / "save.json5"),
            },
        )
    except Exception:
        pass
    
    try:
        manifestPath = rootDir / "save.json5"
        if not manifestPath.exists():
            raise FileNotFoundError(f"Missing manifest: {manifestPath}")
        
        manifest = migrateIfNeeded(readJson5(manifestPath), rootDir)
        
        appPackId = manifest.get("appPackId")
        if not isinstance(appPackId, str) or not appPackId:
            raise ValueError(f"Missing or invalid 'appPackId' in manifest: {manifestPath}")
        
        appInstanceId = manifest.get("appInstanceId")
        if not isinstance(appInstanceId, str) or not appInstanceId:
            raise ValueError(f"Missing or invalid 'appInstanceId' in manifest: {manifestPath}")
        
        tracer.updateTraceContext({
            "appPackId": appPackId,
            "appInstanceId": appInstanceId
        })
        if span is not None:
            try:
                tracer.traceEvent(
                    "appInstance.load.manifest",
                    tags=["appInstance", "load"],
                    span=span,
                    attrs={
                        "appPackId": appPackId,
                        "appInstanceId": appInstanceId,
                    },
                )
            except Exception:
                pass
        
        # 1) Load appInstance instance
        saveFileMeta = manifest.get("files", {}).get("appInstance") or {}
        appInstanceSnapshotFileRelPath = saveFileMeta.get("path")
        if not isinstance(appInstanceSnapshotFileRelPath, str) or not appInstanceSnapshotFileRelPath:
            raise MissingSnapshotProperty("Manifest 'files.appInstance.path' is missing")
        
        appInstanceSnapshotPath = rootDir / appInstanceSnapshotFileRelPath
        if not appInstanceSnapshotPath.exists():
            raise FileNotFoundError(f"Missing appInstance snapshot file '{appInstanceSnapshotPath}'")
        
        # Optional checksum verification
        expectedHash = saveFileMeta.get("sha256")
        if isinstance(expectedHash, str):
            actualHash = sha256Bytes(appInstanceSnapshotPath.read_bytes())
            if expectedHash != actualHash:
                logger.warning(
                    "Checksum failed for '%s' (expected %s, got %s)",
                    appInstanceSnapshotPath,
                    expectedHash,
                    actualHash
                )

        appInstanceSnapshot = readJson5(appInstanceSnapshotPath)
        snapshotAppPackId = appInstanceSnapshot.get("appPackId")
        if isinstance(snapshotAppPackId, str) and snapshotAppPackId and snapshotAppPackId != appPackId:
            logger.warning("Manifest appPackId=%r differs from save file's appPackId=%r", appPackId, snapshotAppPackId)
        
        # Recreate AppInstance shell with proper args per API
        appInstance = AppInstance.fromSnapshot(
            appInstanceSnapshot,
            appPackId=appPackId,
            saveBaseDirectory=rootDir, # Ensures session save paths resolve under this save root
            kernelMemoryLayers=None,
        )
        
        # 2) Recreate sessions from session snapshots
        sessionsMeta = manifest.get("files", {}).get("sessions") or {}
        if not isinstance(sessionsMeta, dict):
            sessionsMeta = {}
        
        # Compute the shared bottom layers (same order as AppInstance.makeSession)
        sharedBottomLayers = [
            appInstance.runtimeMemory,
            appInstance.staticMemory,
            *appInstance.kernelBottom,
        ]
        
        for sessionId, meta in sessionsMeta.items():
            sessionRelPath = meta.get("path")
            if not isinstance(sessionRelPath, str) or not sessionRelPath:
                raise MissingSnapshotProperty(f"Session '{sessionId}': missing 'path' in manifest")
            
            sessionPath = rootDir / sessionRelPath
            if not sessionPath.exists():
                raise FileNotFoundError(f"Missing session snapshot file '{sessionPath}'")
            
            # Optional checksum verification for session snapshot
            expectedSessionHash = meta.get("sha256")
            if isinstance(expectedSessionHash, str):
                actualSessionHash = sha256Bytes(sessionPath.read_bytes())
                if expectedSessionHash != actualSessionHash:
                    logger.warning(
                        "Checksum failed for session '%s' (%s): expected %s, got %s",
                        sessionId, sessionPath, expectedSessionHash, actualSessionHash,
                    )
            
            sessionSnapshot = readJson5(sessionPath)
            
            # Build Session shell from snapshot
            session = Session.fromSnapshot(
                sessionSnapshot,
                sharedBottomLayers=sharedBottomLayers,
                savePath=sessionPath,
            )
            
            # Hydrate session memory layers from its per-layer directory if present
            layersDirStr = meta.get("layersDir")
            layersDir = (
                (rootDir / layersDirStr)
                if isinstance(layersDirStr, str)
                else (sessionPath.parent / f"{session.id}_layers")
            )
            try:
                loadLayersFromDir(session.memoryLayers, layersDir, missingOk=True)
            except Exception:
                logger.exception("Failed to load memory layers for session '%s' from '%s'", session.id, layersDir)
            
            # Register into appInstance
            appInstance.sessionsById[session.id] = session
        
        # 3) Restore main session pointer if present (manifest or appInstance snapshot)
        mainSessionId = manifest.get("mainSessionId") or appInstanceSnapshot.get("mainSessionId")
        if isinstance(mainSessionId, str) and mainSessionId in appInstance.sessionsById:
            appInstance.mainSession = appInstance.sessionsById[mainSessionId]
        # No fallback as main session might be optional for some appInstances
        
        logger.info(
            "Loaded appInstance '%s' (appPack=%r) from '%s' (sessions=%d)",
            appInstance.id,
            appInstance.appPackId,
            str(rootDir),
            len(appInstance.sessionsById),
        )
        
        if span is not None:
            try:
                tracer.traceEvent(
                    "appInstance.load.done",
                    level="info",
                    tags=["appInstance", "load"],
                    span=span,
                    attrs={
                        "appInstanceId": appInstance.id,
                        "appPackId": appInstance.appPackId,
                        "sessionCount": len(appInstance.sessionsById),
                    },
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["appInstance", "load"],
                )
            except Exception:
                pass
        
        return appInstance

    except Exception as err:
        if span is not None:
            try:
                tracer.traceEvent(
                    "appInstance.load.error",
                    level="error",
                    tags=["appInstance", "load", "error"],
                    span=span,
                    attrs={
                        "errorType": type(err).__name__,
                        "errorMessage": str(err),
                    },
                )
                tracer.endSpan(
                    span,
                    status="error",
                    tags=["appInstance", "load", "error"],
                    errorType=type(err).__name__,
                    errorMessage=str(err),
                )
            except Exception:
                pass
        raise
