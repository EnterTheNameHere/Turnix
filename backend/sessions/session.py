# backend/sessions/session.py
from __future__ import annotations
import logging
import time
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from backend.core.ids import uuid_12
from backend.memory.memory_layer import (
    DictMemoryLayer,
    MemoryLayer,
    TransactionalMemoryLayer,
    MemoryResolver,
    LayeredMemory,
    MemoryPropagator,
)
from backend.memory.memory_persistence import saveLayersToFile, loadLayersFromFile
from backend.pipeline.llmpipeline import LLMPipeline

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
        visibility: SessionVisibility = SessionVisibility("public"),
        sharedBottomLayers: list[MemoryLayer] | None = None,
        savePath: Path | str | None = None,
    ):
        self.kind: SessionKind = kind
        self.id: str = sessionId or uuid_12({
            "main": "mainSession_",
            "hidden": "hiddenSession_",
            "temporary": "temporarySession_",
        }[kind])
        self.version: int = 0
        self.createdTs: float = time.time()

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

        self.savePath: Path | None = None
        # Save path resolution
        if savePath is not None:
            path = Path(savePath)
            if path.is_dir():
                # Directory: derive file name from session id
                path = path / f"{self.id}.json5"
            else:
                # File path: use as-is (even if it doesn't exist yet)
                if not path.suffix:
                    # Optional safety — default to .json5 if user gave bare filename
                    path = path.with_suffix(".json5")
            self.savePath = path

        if self.savePath is not None and self.savePath.exists():
            try:
                loadLayersFromFile(self.memoryLayers, self.savePath)
            except Exception:
                logger.exception("Failed to load session memory from %s", self.savePath)

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

        # Authoritative session memory/state (backend)
        self.state: dict[str, Any] = {}
        self.objects: dict[str, Any] = {}

        # Each Session has one orchestration Pipeline
        self.pipeline: LLMPipeline = LLMPipeline(ownerSession=self)

        # Chat storage for conversational history
        self.chat = {
            "threadId": uuid_12("t_"),
            "order": [],          # [oid]
            "headers": {},        # oid -> {role, ts, preview}
            "messages": {},       # oid -> {role, content?, status, ts, runId?}
            "historyPolicy": {
                "strategy": "window",
                "userTail": 6,
                "assistantTail": 6,
                "maxTokens": 2048,
                "alwaysIncludeSystem": True,
            },
            "subs": set() # correlatesTo ids of chat.thread@1 subscribers
                          # NOTE(single-socket): subscription IDs are tracked per-connection
                          # but fanout currently sends only via the caller's ws.
                          # If multiple sockets attach to the same session, they won’t all receive updates.
        }

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
    
    def saveMemory(self) -> None:
        """
        Commit txn to real layers.
        If self.savePath write to disk.
        """
        
        try:
            propagator = MemoryPropagator(self.memoryResolver)
            changes = propagator.commit(self.memoryLayers)

            if self.savePath is None or changes == 0:
                return
            
            saveLayersToFile(self.memoryLayers, self.savePath)
        except Exception:
            logger.exception("Failed to save session memory to %s", self.savePath)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "version": self.version,
            "createdTs": self.createdTs,
            "ownerViewId": self.ownerViewId,
            "visibility": self.visibility,
            "state": self.state,
            "memoryLayers": [layer.name for layer in self.memoryLayers],
        }
    
    def destroy(self, *, persist: bool = False) -> None:
        """
        Cancel all pipeline runs and tear down memory.
        By default we DO NOT persist on destroy, because destroy may be called from
        error/teardown paths where the state is not guaranteed to be consistent.
        """
        try:
            self.pipeline.cancelAllRuns()
        except Exception:
            pass

        try:
            if persist:
                self.saveMemory()
            else:
                propagator = MemoryPropagator(self.memoryResolver)
                propagator.rollback(self.memoryLayers)
        finally:
            self.memoryLayers.clear()
