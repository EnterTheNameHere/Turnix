# backend/pipeline/types.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Literal, TypedDict



class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"



@dataclass
class QueryItem:
    role: MessageRole
    text: str
    oid: str | None = None
    origin: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    status: Literal["draft", "streaming", "final", "error"] = "draft"

    def toOpenAI(self) -> dict[str, str]:
        # Minimal OAIF format. NOTE: Expand here when we support images/tools
        return {"role": self.role.value, "content": self.text or ""}



class ThreadSnapshot(TypedDict, total=False):
    kind: Literal["threadSnapshot"]
    order: list[str]
    headers: dict[str, dict[str, Any]] # oid -> { role, preview, status?, ts? }



class ThreadDelta(TypedDict, total=False):
    kind: Literal["threadDelta"]
    op: Literal["insert", "remove"]
    at: int
    oids: list[str]
    headers: dict[str, dict[str, Any]] | None



class MessageDelta(TypedDict, total=False):
    kind: Literal["messageDelta"]
    oid: str
    textDelta: str | None
    text: str | None
    fields: dict[str, Any] | None
    headers: dict[str, dict[str, Any]] | None



def iterAsOpenAI(items: Iterable[QueryItem]) -> list[dict[str, str]]:
    return [it.toOpenAI() for it in items]
