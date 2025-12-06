# backend/kernel/kernel.py
from __future__ import annotations
from pathlib import Path

from backend.app.context import PROCESS_REGISTRY
from backend.memory.memory_layer import (
    DictMemoryLayer,
    ReadOnlyMemoryLayer,
    MemoryLayer,
)
from backend.runtimes.instance import AppInstance

__all__ = ["Kernel"]



class Kernel:
    """
    Top-level backend owner.
    - Exists even if no appInstance is active.
    - Owns process-wide memory (mods, assets, pack-level config).
    - Can switch active appInstance (game, main menu, headless).
    """
    def __init__(self) -> None:
        # Make globally discoverable
        PROCESS_REGISTRY.register("kernel", self, overwrite=True)
        
        # Process-wide memory
        self.kernelRuntimeMemory: MemoryLayer = DictMemoryLayer("kernelRuntime")
        self.kernelStaticMemory:  MemoryLayer = ReadOnlyMemoryLayer("kernelStatic", {})
        self.activeAppInstance: AppInstance | None = None

    def createAppInstance(
        self,
        *,
        appPackId: str,
        appInstanceId: str | None = None,
        saveBaseDirectory: Path | str | None = None,
        createMainSession: bool = True,
    ) -> AppInstance:
        """
        Factory for a plain AppInstance that is already wired to kernel layers.
        """
        appInstance = AppInstance(
            appPackId=appPackId,
            appInstanceId=appInstanceId,
            kernelMemoryLayers=self.getKernelBottomLayers(),
            saveBaseDirectory=saveBaseDirectory,
            createMainSession=createMainSession,
        )
        self.switchAppInstance(appInstance)
        return appInstance

    def switchAppInstance(self, appInstance: AppInstance) -> None:
        """
        Activate given appInstance (game, menu, headless...).
        """
        self.activeAppInstance = appInstance
        PROCESS_REGISTRY.register("appInstance.active", appInstance, overwrite=True)
    
    def getActiveAppInstance(self) -> AppInstance | None:
        return self.activeAppInstance
    
    def getKernelBottomLayers(self) -> list[MemoryLayer]:
        """
        Layers that every appInstance may want to inherit.
        """
        return [self.kernelRuntimeMemory, self.kernelStaticMemory]

