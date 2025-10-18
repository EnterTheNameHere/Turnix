# backend/rpc/models.py
from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, Field, ConfigDict, model_validator
from pydantic.alias_generators import to_camel
from backend.core.time import nowMonotonicMs

__all__ = ["Gen", "Route", "RPCMessage"]



class Gen(BaseModel):
    """Server-assigned generation for a connection."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )
    num: int
    salt: str



class Route(BaseModel):
    """RPC addressing: capability or object."""
    capability: str | None = None
    object: str | None = None



class RPCMessage(BaseModel):
    """Canonical RPC wire message."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    v: str                          # RPCMessage schema version
    id: str                         # UUIDv7
    type: Literal["ack","heartbeat","hello","welcome","clientReady","request","emit","reply","subscribe","stateUpdate","unsubscribe","cancel","error"]
    correlatesTo: str | None = None # UUIDv7 of previous message, if in sequence.
    gen: Gen                        # generation of connection as set by server
    ts: int = Field(default_factory=nowMonotonicMs) # Monotonic time of sending
    budgetMs: int | None = None     # How many ms to finish job and communication
    ackOf: int | None = None
    job: dict[str, Any] | None = None # Represents current status of job being executed
    idempotencyKey: str | None = None
    route: Route | None = None      # "Address" of handler which should be handling the message
    op: str | None = None           # "operation" handler should perform, if further specification is needed
    path: str | None = None         # Additional info for handler to decide which "operation" to execute
    args: list[Any] | None = None   # "arguments" for "operation" handler might find useful to decide what "operation" to execute
    seq: int | None = None          # Per-lane delivery sequence number
    origin: dict[str, Any] | None = None # For metadata only, not for auth
    chunkNo: int | None = None      # For streamed payload
    final: int | None = None        # For streamed payload
    payload: dict[str, Any] = Field(default_factory=dict)
    
    # Non-optional with a default value
    lane: str = Field(default="noLaneSet") # "sys" or other lane name

    # --------------
    #   Validators  
    # --------------
    @model_validator(mode="after")
    def fillDefaults(self):
        # lane fallback based on route
        if not self.lane or self.lane == "noLaneSet":
            if self.route:
                if self.route.capability is not None:
                    self.lane = f"cap:{self.route.capability}"
                elif self.route.object is not None:
                    self.lane = f"obj:{self.route.object}"
                else:
                    self.lane = "noValidRouteLane"
            else:
                self.lane = "noLaneSet"
        
        return self


