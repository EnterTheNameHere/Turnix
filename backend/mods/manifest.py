# backend/mods/manifest.py
from __future__ import annotations
from pydantic import BaseModel, Field, ConfigDict

__all__ = ["RuntimeSpec", "ModManifest"]



class RuntimeSpec(BaseModel):
    """Defines how a specific runtime (JS/Python) should load a mod."""
    model_config = ConfigDict(extra="forbid")

    entry: str
    enabled: bool = True
    order: int = 0
    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)



class ModManifest(BaseModel):
    """Represents a validated mod manifest."""
    model_config = ConfigDict(extra="forbid")
    
    kind: str
    id: str
    displayName: str
    version: str
    description: str | None = None
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    hidden: bool = False
    runtimes: dict[str, RuntimeSpec]
    assets: list[str] = Field(default_factory=list)
