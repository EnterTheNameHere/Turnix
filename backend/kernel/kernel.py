# backend/kernel/kernel.py
from __future__ import annotations
from pathlib import Path

from backend.app.context import PROCESS_REGISTRY
from backend.memory.memory_layer import (
    DictMemoryLayer,
    ReadOnlyMemoryLayer,
    MemoryLayer,
)
from backend.runtimes.instance import RuntimeInstance

__all__ = ["Kernel"]



class Kernel:
    """
    Top-level backend owner.
    - Exists even if no runtime is active.
    - Owns process-wide memory (mods, assets, pack-level config).
    - Can switch active runtimes (game, main menu, headless).
    """
    def __init__(self) -> None:
        # Make globally discoverable
        PROCESS_REGISTRY.register("kernel", self, overwrite=True)
        
        # Process-wide memory
        self.kernelRuntimeMemory: MemoryLayer = DictMemoryLayer("kernelRuntime")
        self.kernelStaticMemory:  MemoryLayer = ReadOnlyMemoryLayer("kernelStatic", {})
        self.activeRuntime: RuntimeInstance | None = None

    def createRuntime(
        self,
        *,
        appPackId: str,
        runtimeId: str | None = None,
        saveBaseDirectory: Path | str | None = None,
        createMainSession: bool = True,
    ) -> RuntimeInstance:
        """
        Factory for a plain BaseRuntime that is already wired to kernel layers.
        """
        br = RuntimeInstance(
            appPackId=appPackId,
            runtimeInstanceId=runtimeId,
            kernelMemoryLayers=self.getKernelBottomLayers(),
            saveBaseDirectory=saveBaseDirectory,
            createMainSession=createMainSession,
        )
        self.switchRuntime(br)
        return br

    def switchRuntime(self, runtime: RuntimeInstance) -> None:
        """
        Activate given runtime (game, menu, headless...).
        """
        self.activeRuntime = runtime
        PROCESS_REGISTRY.register("runtime.active", runtime, overwrite=True)
    
    def getActiveRuntime(self) -> RuntimeInstance | None:
        return self.activeRuntime
    
    def getKernelBottomLayers(self) -> list[MemoryLayer]:
        """
        Layers that every runtime may want to inherit.
        """
        return [self.kernelRuntimeMemory, self.kernelStaticMemory]

