# backend/rpc/types.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from backend.rpc.models import RPCMessage

__all__ = ["SubscriptionEntry", "PendingRequestEntry"]



@dataclass(slots=True)
class SubscriptionEntry:
    task: asyncio.Task[Any]
    onCancel: Callable[[], None] | None
    signal: asyncio.Event



@dataclass(slots=True)
class PendingRequestEntry:
    task: asyncio.Task[Any]
    msg: RPCMessage
