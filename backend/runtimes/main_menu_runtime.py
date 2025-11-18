# backend/runtimes/main_menu_runtime.py
from __future__ import annotations
from pathlib import Path
from typing import Any

from backend.app.globals import getRootsService
from backend.config.providers import (
    DefaultsProvider,
    FileProvider,
    RuntimeProvider,
    ViewProvider,
)
from backend.config.service import ConfigService
from backend.config.store import ConfigStore
from backend.core.schema_registry import SchemaRegistry
from backend.memory.memory_layer import MemoryLayer
from backend.runtimes.instance import RuntimeInstance

__all__ = ["MainMenuRuntime"]



class MainMenuRuntime(RuntimeInstance):
    """
    Launcher/menu state that exists when no AppRuntime is active.
    Owns a main Session so we can run helper pipelines (LLM tips, save search, etc.)
    """
    def __init__(
        self,
        *,
        configService: ConfigService,
        configRegistry: SchemaRegistry,
        globalConfigView: ConfigStore,
        appPackId: str = "Turnix@main_menu",
        runtimeInstanceId: str = "turnix_main_menu",
        kernelMemoryLayers: list[MemoryLayer] | None = None,
        saveBaseDirectory: str | Path | None = None,
    ) -> None:
        if saveBaseDirectory is None:
            # Default: keep main menu saves within first-party/main_menu/saves
            assetsRoots = getRootsService().rootsFor("first-party")
            if not assetsRoots:
                # Fallback to generic saves root if assets missing (shouldn't happen with validated repo)
                saveBaseDirectory = getRootsService().getWriteDir("saves")
            else:
                saveBaseDirectory = assetsRoots[0] / "main_menu" / "saves"
        
        super().__init__(
            appPackId=appPackId,
            runtimeInstanceId=runtimeInstanceId,
            kernelMemoryLayers=kernelMemoryLayers,
            saveBaseDirectory=saveBaseDirectory,
            createMainSession=True,
        )

        self.config = self._initConfig(configRegistry, globalConfigView)
        self.configService = configService
        self.globalConfig: ConfigStore = self.configService.globalStore

        self.recentSaves: list[dict[str, Any]] = []

    def _initConfig(self, reg: SchemaRegistry, globalConfig: ConfigStore) -> ConfigStore:
        validator = reg.getValidator("config", "runtime")

        # Reuse runtime's save root
        savePath = self.saveRoot / "config.json5"
        
        defaultsPath = getRootsService().rootsFor("first-party")[0] / "config" / "defaults" / "global.json5"

        providers = [
            DefaultsProvider(path=str(defaultsPath)),  # Runtime defaults
            # A "view provider" that reads from global (read-only)
            ViewProvider(globalConfig),   # Inherit global values as a lower layer (read-only)
            FileProvider(path=str(savePath), readOnly=False),
            RuntimeProvider(),
        ]
        return ConfigStore(namespace="config:runtime", validator=validator, providers=providers)

    def addRecentSave(self, entry: dict[str, Any], keep: int = 10):
        self.recentSaves.insert(0, entry)
        if len(self.recentSaves) > keep:
            del self.recentSaves[keep:]
