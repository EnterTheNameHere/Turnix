# backend/kernel/kernel.py
from __future__ import annotations
from pathlib import Path

from backend.memory.memory_layer import (
    DictMemoryLayer,
    ReadOnlyMemoryLayer,
    MemoryLayer,
)
from backend.runtimes.base import BaseRuntime



class Kernel:
    """
    Top-level backend owner.
    - Exists even if no runtime is active.
    - Owns process-wide memory (mods, assets, pack-level config).
    - Can switch active runtimes (game, main menu, headless).
    """
    def __init__(self) -> None:
        # Process-wide memory
        self.kernelRuntimeMemory: MemoryLayer = DictMemoryLayer("kernelRuntime")
        self.kernelStaticMemory:  MemoryLayer = ReadOnlyMemoryLayer("kernelStatic", {})
        self.activeRuntime: BaseRuntime | None = None

    def createRuntime(
        self,
        *,
        runtimeId: str | None = None,
        saveRoot: Path | str | None = None,
    ) -> BaseRuntime:
        """
        Factory for a plain BaseRuntime that is already wired to kernel layers.
        """
        br = BaseRuntime(
            runtimeId=runtimeId,
            kernelMemoryLayers=self.getKernelBottomLayers(),
            saveRoot=saveRoot,
        )
        self.switchRuntime(br)
        return br

    def switchRuntime(self, runtime: "BaseRuntime") -> None:
        """
        Activate given runtime (game, menu, headless...).
        """
        self.activeRuntime = runtime
    
    def getActiveRuntime(self) -> "BaseRuntime | None":
        return self.activeRuntime
    
    def getKernelBottomLayers(self) -> list[MemoryLayer]:
        """
        Layers that every runtime may want to inherit.
        """
        return [self.kernelRuntimeMemory, self.kernelStaticMemory]

