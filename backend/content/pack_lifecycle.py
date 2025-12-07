# backend/content/pack_lifecycle.py
from __future__ import annotations

from enum import Enum
from collections.abc import Iterable

from backend.content.pack_descriptor import PackDescriptor

__all__ = [
    "PackLifecyclePhase",
    "PackLifecycleListener",
]



class PackLifecyclePhase(str, Enum):
    """
    High-level lifecycle phase of a pack within the engine.
    
    These phases are intended primarily for tracing, metrics, and
    lifecycle listeners. Not every pack will necessarily go through
    or reach all phases in a given run.
    """
    DISCOVERED = "discovered"   # Manifest found, PackDescriptor build
    INDEXED = "indexed"         # Added to PackDescriptorRegistry
    SELECTED = "selected"       # Chosen by PackManager for this run
    PREPARED = "prepared"       # Config schemas + config + runtime entries ready
    ACTIVATED = "activated"     # Code is live (mods loaded / views wired)



class PackLifecycleListener:
    """
    Optional hook interface for systems that want to observe pack lifecycle.
    
    Implementations may override any subset of methods. All methods have
    safe no-op defaults.
    """
    
    def onPacksDiscovered(self, packs: Iterable[PackDescriptor]) -> None:
        """
        Called after a batch of packs has been discovered from roots and
        PackDescriptor instances created (but before registry indexing).
        """
        # Default: no-op
        return
    
    def onPackIndexed(self, desc: PackDescriptor) -> None:
        """
        Call when a pack is registered in PackDescriptorRegistry.
        """
        # Default: no-op
        return
    
    def onPackSelected(self, desc: PackDescriptor) -> None:
        """
        Called when PackManager decides this pack participates in the
        current run (app, view, mods, system packs).
        """
        # Default: no-op
        return
    
    def onPackPrepared(self, desc: PackDescriptor) -> None:
        """
        Called after config schemas and default/user config have been
        loaded for this pack and runtime entries resolved.
        """
        # Default: no-op
        return
    
    def onPackActivated(self, desc: PackDescriptor) -> None:
        """
        Called when the pack's code is live. For mods, this means
        onLoad() has been called. For app/view packs, this means their
        runtime is ready to serve requests.
        """
        # Default: no-op
        return
