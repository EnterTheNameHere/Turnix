# backend/sessions/session.py
from __future__ import annotations
import logging
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from backend.app.globals import getTracer
from backend.core.ids import uuid_12
from backend.memory.memory_layer import (
    DictMemoryLayer,
    MemoryLayer,
    TransactionalMemoryLayer,
    MemoryResolver,
    LayeredMemory,
    MemoryPropagator,
)
from backend.memory.memory_persistence import loadLayersFromDir
from backend.memory.memory_save_manager import MemorySavePolicy, MemorySaveManager

logger = logging.getLogger(__name__)

__all__ = ["Session", "SessionKind", "SessionVisibility"]



class SessionKind(StrEnum):
    MAIN = "main"
    HIDDEN = "hidden"
    TEMPORARY = "temporary"



class SessionVisibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"



class Session:
    """
    Execution / world context with its own state, memory, and LLM pipeline.
    Each Session can see multiple memory layers, injected by its parent Runtime or Kernel.

    Kinds:
      - "main": authoritative
      - "hidden": private scratch context (e.g. for background reasoning); only available to a single mod, with opt-in
      - "temporary": public shared short-lived context; all mods are notified on its creation
    """
    def __init__(
        self,
        *,
        kind: SessionKind,
        sessionId: str | None = None,
        ownerViewId: str | None = None,
        visibility: SessionVisibility = SessionVisibility.PUBLIC,
        sharedBottomLayers: list[MemoryLayer] | None = None,
        savePath: Path | str | None = None,
    ):
        from backend.pipeline.llmpipeline import LLMPipeline
        
        self.kind: SessionKind = kind
        self.id: str = sessionId or uuid_12({
            "main": "mainSession_",
            "hidden": "hiddenSession_",
            "temporary": "temporarySession_",
        }[kind.value])
        self.version: int = 0
        self.createdTs: float = time.time()
        self._traceSpan = None
        
        tracer = getTracer()
        tracer.updateTraceContext({
            "sessionId": self.id,
            "sessionKind": self.kind.value,
        })
        try:
            self._traceSpan = tracer.startSpan(
                "session.lifecycle",
                attrs={
                    "kind": self.kind.value,
                    "visibility": visibility.value,
                },
                tags=["session"],
                contextOverrides={
                    "sessionId": self.id,
                    "sessionKind": self.kind.value,
                },
            )
            tracer.traceEvent(
                "session.create",
                attrs={
                    "sessionId": self.id,
                    "kind": self.kind.value,
                    "visibility": visibility.value,
                    "ownerViewId": ownerViewId,
                },
                level="info",
                tags=["session"],
                span=self._traceSpan,
            )
        except Exception:
            # Tracing must never break session construction.
            self._traceSpan = None

        # Access metadata
        self.ownerViewId: str | None = ownerViewId
        self.visibility: SessionVisibility = visibility

        # ----- Memory stack (real layers) -----
        # Order (top-bottom) for reads:
        #   0) txn
        #   1) session
        #   2) runtime
        #   3) static
        #   4) kernelRuntime
        #   5) kernelStatic
        self.txnMemoryLayer: TransactionalMemoryLayer = TransactionalMemoryLayer("txn")
        self.sessionMemory: MemoryLayer = DictMemoryLayer(f"session:{self.id}")
        self.memoryLayers: list[MemoryLayer] = [
            self.txnMemoryLayer,
            self.sessionMemory,
            *(sharedBottomLayers or [])
        ]

        # Save path resolution
        self.savePath: Path | None = None
        self.layersSaveDir: Path | None = None
        if savePath is not None:
            basePath = Path(savePath)
            if basePath.is_dir():
                # Directory: derive file name from session id
                self.savePath = basePath / f"{self.id}.json5"
            else:
                self.savePath = basePath if basePath.suffix else basePath.with_suffix(".json5")
            # Per-layer dir next to session file
            self.layersSaveDir = self.savePath.parent / f"{self.id}_layers"

        # Load existing per-layer data if any
        if self.layersSaveDir is not None:
            try:
                loadLayersFromDir(self.memoryLayers, self.layersSaveDir, missingOk=True)
            except Exception:
                logger.exception("Failed to load session memory from %s", self.layersSaveDir)

        # Resolver: map user-friendly prefixes to actual layer names
        nsMap: dict[str, str] = {
            "txn": "txn",
            "session": self.sessionMemory.name,
        }

        for layer in sharedBottomLayers or []:
            # Include layers from sharedBottomLayers in resolver
            nsMap[layer.name] = layer.name

        self.memoryResolver = MemoryResolver(nsMap)

        # Facade: What the mods/pipeline can see and use
        self.memory = LayeredMemory(
            layers=self.memoryLayers,
            resolver=self.memoryResolver,
            txn=self.txnMemoryLayer,
        )

        # Orchestration
        self.state: dict[str, Any] = {}
        self.objects: dict[str, Any] = {}
        self.pipeline: LLMPipeline = LLMPipeline(ownerSession=self)

        # ----- Per-layer save manager -----
        self.saveManager: MemorySaveManager | None = None
        if self.layersSaveDir is not None:
            mgr = MemorySaveManager(self.layersSaveDir)
            # Register all non-txn DictMemoryLayers. Default policy: save immediately on change.
            for layer in self.memoryLayers:
                if isinstance(layer, TransactionalMemoryLayer):
                    continue
                mgr.registerLayer(layer, fileName=f"{layer.name}.json5",
                                  policy=MemorySavePolicy()) # Tweak per layer if needed
            self.saveManager = mgr

    # ------------------------------------------------------------------ #
    # Memory operations (delegation to the layer stack)
    # ------------------------------------------------------------------ #

    def get(self, key: str) -> Any | None:
        obj = self.memory.get(key)
        return obj.payload if obj is not None else None
    
    def set(self, key: str, value: Any) -> None:
        """
        Convenience write: stage a simple value into txn under 'session.<key>'.
        Prefer session.memory.save(...) for QueryItems / richer objects.
        """
        from backend.memory.memory_layer import MemoryObject
        fullName = f"session.{key}"
        memObj = MemoryObject(
            id=key,
            payload=value,
            path=f"session.{key}",
            originLayer=self.sessionMemory.name
        )
        # Stage to txn, real write happens on save/commit
        self.txnMemoryLayer.set(fullName, memObj)
        self.version += 1
    
    def saveMemory(self) -> bool:
        """
        Commit txn to real layers and persist changed layers per policy.
        Raise on commit/persist failure.
        Returns True on success.
        """
        propagator = MemoryPropagator(self.memoryResolver)
        result = propagator.commit(self.memoryLayers)

        # Per-layer autosave (may raise)
        if self.saveManager is not None and not result.isEmpty():
            self.saveManager.onCommitted(result)
            
        # Defensive: txn should be empty after a successful commit
        txn = self.txnMemoryLayer
        if txn.staged or txn.changes:
            raise RuntimeError("commitIncomplete")
        
        self.version += 1
        
        return True
            
    
    def flushLayer(self, layerName: str) -> bool:
        return self.saveManager.flushLayer(layerName) if self.saveManager else False

    # ------------------------------------------------------------------ #
    # Persistence policy control
    # ------------------------------------------------------------------ #
    
    def registerLayerPolicy(
        self,
        layerName: str,
        *,
        debounceMs: int = 0,
        maxIntervalMs: int = 0,
        maxDirtyItems: int = 0,
    ) -> bool:
        """
        Override save policy for a registered (non-txn) layer at runtime.
        Returns True if a policy was updated, False if the layer wasn't registered.
        """
        if self.saveManager is None:
            return False
        reg = self.saveManager.byName.get(layerName)
        if reg is None:
            return False
        reg.policy = MemorySavePolicy(
            debounceMs=debounceMs,
            maxIntervalMs=maxIntervalMs,
            maxDirtyItems=maxDirtyItems,
        )
        return True
    
    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "version": self.version,
            "createdTs": self.createdTs,
            "ownerViewId": self.ownerViewId,
            "visibility": self.visibility.value,
            "state": self.state,
            "memoryLayers": [layer.name for layer in self.memoryLayers],
            "savePath": str(self.savePath) if self.savePath is not None else None,
        }
    
    @classmethod
    def fromSnapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        sharedBottomLayers: list[MemoryLayer] | None = None,
        savePath: Path | str | None = None,
    ) -> Session:
        """
        Rehydrate a Session with the expected memory stack and resolver.
        Callers (e.g., RuntimeInstance.fromSnapshot) should pass the bottom layers and savePath.
        """
        try:
            kind = SessionKind(snapshot.get("kind", SessionKind.TEMPORARY))
            visibility = SessionVisibility(snapshot.get("visibility", SessionVisibility.PUBLIC))
            
            session = cls(
                kind=kind,
                sessionId=snapshot.get("id"),
                ownerViewId=snapshot.get("ownerViewId"),
                visibility=visibility,
                sharedBottomLayers=sharedBottomLayers,
                savePath=savePath,
            )
            
            session.version = int(snapshot.get("version", 0))
            session.createdTs = float(snapshot.get("createdTs", time.time()))
            session.state = snapshot.get("state", {})
            return session
        except Exception:
            logger.exception("Error creating session from snapshot")
            raise
    
    def destroy(self, *, persist: bool = False) -> None:
        """
        Cancel all pipeline runs and tear down memory.
        By default we DO NOT persist on destroy, because destroy may be called from
        error/teardown paths where the state is not guaranteed to be consistent.
        """
        status = "ok"
        try:
            try:
                try:
                    self.pipeline.cancelAllRuns()
                except Exception:
                    pass

                try:
                    if persist:
                        # Flush everything once (propagate exceptions to caller)
                        try:
                            self.saveMemory()
                            if self.saveManager is not None:
                                self.saveManager.flushAll()
                        except Exception:
                            # On failure, try rollback and continue teardown
                            try:
                                propagator = MemoryPropagator(self.memoryResolver)
                                propagator.rollback(self.memoryLayers)
                            except Exception:
                                logger.exception("Error rolling back memory")
                                pass
                            raise
                    else:
                        try:
                            propagator = MemoryPropagator(self.memoryResolver)
                            propagator.rollback(self.memoryLayers)
                        except Exception:
                            # Best effort teardown even if resolver/layers are already half-torn
                            logger.exception("Error rolling back memory")
                            pass
                finally:
                    self.memoryLayers.clear()
            except Exception:
                status = "error"
                raise
        finally:
            span = getattr(self, "_traceSpan", None)
            if span is not None:
                tracer = getTracer()
                try:
                    tracer.traceEvent(
                        "session.destroy",
                        attrs={
                            "sessionId": self.id,
                            "persist": persist,
                            "status": status,
                        },
                        level="info",
                        tags=["session"],
                        span=span,
                    )
                    traceStatus = "ok" if status == "ok" else "error"
                    tracer.endSpan(
                        span,
                        status=traceStatus,
                        level="info",
                        tags=["session"],
                        attrs={
                            "destroyedTs": time.time(),
                            "persist": persist,
                            "finalStatus": status,
                        },
                    )
                except Exception:
                    # Tracing must not interfere with teardown.
                    pass
                self._traceSpan = None
