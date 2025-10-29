# backend/sessions/session.py
from __future__ import annotations
import time
from typing import Any, Literal

from backend.core.ids import uuid_12
from backend.pipeline.llmpipeline import LLMPipeline

__all__ = ["Session"]



class Session:
    """
    World/inference context with memory; owns a single Pipeline.
    Kinds:
      - "main": world-authoritative timeline, created in GameRealm.
      - "hidden": private scratch context (usually ownerViewId-bound)
      - "temporary": publicly announced, shared short-lived context.
      - "shell": app menu / launcher context (no realm loaded).
    """
    def __init__(
        self,
        *,
        kind: Literal["main", "hidden", "temporary", "shell"],
        sessionId: str | None = None,
        ownerViewId: str | None = None,
        visibility: Literal["public", "private"] = "public",
    ):
        self.kind: Literal["main", "hidden", "temporary", "shell"] = kind
        self.id: str = sessionId or uuid_12({"main": "ms_", "hidden": "hs_", "temporary": "ts_", "shell": "sh_"}[kind])
        self.version: int = 0
        self.createdTs: float = time.time()

        # Access control / discovery metadata
        self.ownerViewId: str | None = ownerViewId
        self.visibility: Literal["public", "private"] = visibility

        # Authoritative session memory/state (backend)
        self.state: dict[str, Any] = {}
        self.objects: dict[str, Any] = {}

        # Each Session has one orchestration Pipeline
        self.pipeline: LLMPipeline = LLMPipeline(ownerSession=self)

        # Chat store
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

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "version": self.version,
            "createdTs": self.createdTs,
            "ownerViewId": self.ownerViewId,
            "visibility": self.visibility,
            "state": self.state,
            "objects": list(self.objects.keys()),
        }
    
    def destroy(self) -> None:
        # Cancel ongoing pipeline runs
        try:
            self.pipeline.cancelAllRuns()
        except Exception:
            pass
