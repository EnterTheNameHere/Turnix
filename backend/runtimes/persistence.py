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
from backend.runtimes.instance import RuntimeInstance
from backend.sessions.session import Session

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Layout under a save directory
# ------------------------------------------------------------------ #
# save.json5                         # top level manifest + file index + metadata
# state/runtime.json5                # runtime snapshot
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
    runtimeInstance: RuntimeInstance,
    targetDir: Path | str,
    *,
    label: str | None = None,
    thumbnail: bytes | None = None,
) -> tuple[Path, str]:
    """
    Saves a RuntimeInstance into targetDir.
    Returns (path to 'save.json5', sha256).
    """
    tracer = getTracer()
    span = None
    try:
        tracer.updateTraceContext({
            "runtimeInstanceId": runtimeInstance.id,
            "appPackId": runtimeInstance.appPackId,
        })
        span = tracer.startSpan(
            "runtime.save",
            attrs={
                "label": label or "",
                "targetDir": str(targetDir),
            },
            tags=["runtime", "save"],
        )
        tracer.traceEvent(
            "runtime.save.start",
            level="info",
            tags=["runtime", "save"],
            span=span,
            attrs={
                "runtimeInstanceId": runtimeInstance.id,
                "appPackId": runtimeInstance.appPackId,
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
        
        filesIndex = {"runtime": {}, "sessions": {}}
        
        # 1) Write runtime snapshot
        runtimeSnapshot = runtimeInstance.snapshot()
        runtimeFilePath = stateDir / "runtime.json5"
        runtimeHash = writeTextJson5(runtimeFilePath, runtimeSnapshot)
        filesIndex["runtime"]["path"] = toRelPath(root, runtimeFilePath)
        filesIndex["runtime"]["sha256"] = runtimeHash
        
        # 2) Write each session snapshot + per-layer directory
        sessionHashes: dict[str, str] = {}
        for sessionId, session in runtimeInstance.sessionsById.items():
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
            "appPackId": runtimeInstance.appPackId,
            "runtimeInstanceId": runtimeInstance.id,
            "createdTs": int(runtimeInstance.createdTs),
            "savedTs": now,
            "label": label or "",
            "appVersion": "0.0.0", # TODO: Fill when we have build/version system
            "turnixVersion": "0.0.0", # TODO: Fill when we have build/version system
            "files": filesIndex,
            # Convenience for quick restore
            "mainSessionId": runtimeInstance.mainSession.id if runtimeInstance.mainSession else None,
        }
        
        manifestPath = root / "save.json5"
        manifestHash = writeTextJson5(manifestPath, manifest)
        
        logger.info(
            "Saved runtime '%s' (appPack=%r) to %s (sessions=%d)",
            runtimeInstance.id,
            runtimeInstance.appPackId,
            str(root),
            len(runtimeInstance.sessionsById),
        )
        
        if span is not None:
            try:
                tracer.traceEvent(
                    "runtime.save.done",
                    tags=["runtime", "save"],
                    level="info",
                    span=span,
                    attrs={
                        "manifestPath": str(manifestPath),
                        "sessionCount": len(runtimeInstance.sessionsById),
                    },
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["runtime", "save"],
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
                    "runtime.save.error",
                    level="error",
                    tags=["runtime", "save", "error"],
                    span=span,
                    attrs={
                        "errorType": type(err).__name__,
                        "errorMessage": str(err),
                    },
                )
                tracer.endSpan(
                    span,
                    status="error",
                    tags=["runtime", "save", "error"],
                    errorType=type(err).__name__,
                    errorMessage=str(err),
                )
            except Exception:
                pass
        raise


# ------------------------------------------------------------------ #
# Load
# ------------------------------------------------------------------ #

def loadRuntime(sourceDir: Path | str) -> RuntimeInstance:
    """
    Loads a RuntimeInstance from sourceDir.
    Validates manifest, performs migrations, reconstructs runtime + sessions from snapshots,
    and hydrates session memory layers from per-layer directories.
    """
    tracer = getTracer()
    span = None
    rootDir = Path(sourceDir)
    
    try:
        # We do not know appPackId/runtimeInstanceId yet. Set them after manifest loads.
        span = tracer.startSpan(
            "runtime.load",
            attrs={
                "sourceDir": str(rootDir),
            },
            tags=["runtime", "load"],
        )
        tracer.traceEvent(
            "runtime.load.start",
            level="info",
            tags=["runtime", "load"],
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
        
        runtimeInstanceId = manifest.get("runtimeInstanceId")
        if not isinstance(runtimeInstanceId, str) or not runtimeInstanceId:
            raise ValueError(f"Missing or invalid 'runtimeInstanceId' in manifest: {manifestPath}")
        
        tracer.updateTraceContext({
            "appPackId": appPackId,
            "runtimeInstanceId": runtimeInstanceId
        })
        if span is not None:
            try:
                tracer.traceEvent(
                    "runtime.load.manifest",
                    tags=["runtime", "load"],
                    span=span,
                    attrs={
                        "appPackId": appPackId,
                        "runtimeInstanceId": runtimeInstanceId,
                    },
                )
            except Exception:
                pass
        
        # 1) Load runtime instance
        runtimeFileMeta = manifest.get("files", {}).get("runtime") or {}
        runtimeFileRelPath = runtimeFileMeta.get("path")
        if not isinstance(runtimeFileRelPath, str) or not runtimeFileRelPath:
            raise MissingSnapshotProperty("Manifest 'files.runtime.path' is missing")
        
        runtimePath = rootDir / runtimeFileRelPath
        if not runtimePath.exists():
            raise FileNotFoundError(f"Missing runtime snapshot file '{runtimePath}'")
        
        # Optional checksum verification
        expectedHash = runtimeFileMeta.get("sha256")
        if isinstance(expectedHash, str):
            actualHash = sha256Bytes(runtimePath.read_bytes())
            if expectedHash != actualHash:
                logger.warning("Checksum failed for '%s' (expected %s, got %s)", runtimePath, expectedHash, actualHash)

        runtimeSnapshot = readJson5(runtimePath)
        snapshotAppPackId = runtimeSnapshot.get("appPackId")
        if isinstance(snapshotAppPackId, str) and snapshotAppPackId and snapshotAppPackId != appPackId:
            logger.warning("Manifest appPackId=%r differs from save file's appPackId=%r", appPackId, snapshotAppPackId)
        
        # Recreate RuntimeInstance shell with proper args per API
        runtimeInstance = RuntimeInstance.fromSnapshot(
            runtimeSnapshot,
            appPackId=appPackId,
            saveBaseDirectory=rootDir, # Ensures session save paths resolve under this save root
            kernelMemoryLayers=None,
        )
        
        # 2) Recreate sessions from session snapshots
        sessionsMeta = manifest.get("files", {}).get("sessions") or {}
        if not isinstance(sessionsMeta, dict):
            sessionsMeta = {}
        
        # Compute the shared bottom layers (same order as RuntimeInstance.makeSession)
        sharedBottomLayers = [
            runtimeInstance.runtimeMemory,
            runtimeInstance.staticMemory,
            *runtimeInstance.kernelBottom,
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
            layersDir = (rootDir / layersDirStr) if isinstance(layersDirStr, str) else (sessionPath.parent / f"{session.id}_layers")
            try:
                loadLayersFromDir(session.memoryLayers, layersDir, missingOk=True)
            except Exception:
                logger.exception("Failed to load memory layers for session '%s' from '%s'", session.id, layersDir)
            
            # Register into runtime
            runtimeInstance.sessionsById[session.id] = session
        
        # 3) Restore main session pointer if present (manifest or runtime snapshot)
        mainSessionId = manifest.get("mainSessionId") or runtimeSnapshot.get("mainSessionId")
        if isinstance(mainSessionId, str) and mainSessionId in runtimeInstance.sessionsById:
            runtimeInstance.mainSession = runtimeInstance.sessionsById[mainSessionId]
        # No fallback as main session might be optional for some runtimes
        
        logger.info(
            "Loaded runtime '%s' (appPack=%r) from '%s' (sessions=%d)",
            runtimeInstance.id,
            runtimeInstance.appPackId,
            str(rootDir),
            len(runtimeInstance.sessionsById),
        )
        
        if span is not None:
            try:
                tracer.traceEvent(
                    "runtime.load.done",
                    level="info",
                    tags=["runtime", "load"],
                    span=span,
                    attrs={
                        "runtimeInstanceId": runtimeInstance.id,
                        "appPackId": runtimeInstance.appPackId,
                        "sessionCount": len(runtimeInstance.sessionsById),
                    },
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["runtime", "load"],
                )
            except Exception:
                pass
        
        return runtimeInstance

    except Exception as err:
        if span is not None:
            try:
                tracer.traceEvent(
                    "runtime.load.error",
                    level="error",
                    tags=["runtime", "load", "error"],
                    span=span,
                    attrs={
                        "errorType": type(err).__name__,
                        "errorMessage": str(err),
                    },
                )
                tracer.endSpan(
                    span,
                    status="error",
                    tags=["runtime", "load", "error"],
                    errorType=type(err).__name__,
                    errorMessage=str(err),
                )
            except Exception:
                pass
        raise
