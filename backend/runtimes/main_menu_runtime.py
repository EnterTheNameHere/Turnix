# backend/runtimes/main_menu_runtime.py
from __future__ import annotations
from typing import Any

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
from backend.sessions.session import Session

__all__ = ["MainMenuRuntime"]



class MainMenuRuntime(RuntimeInstance):
    """
    Launcher/menu state that exists when no AppRuntime is active.
    Owns a main Session so we can run helper pipelines (LLM tips, save search, etc.)
    """
    def __init__(
        self,
        *,
        runtimeId: str | None = None,
        configService: ConfigService,
        kernelMemoryLayers: list[MemoryLayer] | None = None,
        configRegistry: SchemaRegistry,
        globalConfigView: ConfigStore,
    ) -> None:
        from backend.sessions.session import Session

        super().__init__(runtimeId=runtimeId, kernelMemoryLayers=kernelMemoryLayers)

        self.config = self._initConfig(configRegistry, globalConfigView)
        self.configService = configService
        self.globalConfig: ConfigStore = self.configService.globalStore
        
        self.mainSession: Session = self.makeSession(kind="main")
        
        self.recentSaves: list[dict[str, Any]] = []

    def _initConfig(self, reg: SchemaRegistry, globalConfig: ConfigStore) -> ConfigStore:
        validator = reg.getValidator("config", "runtime")

        # reuse runtime's save root
        savePath = self.saveRoot / "config.json5"

        providers = [
            DefaultsProvider(path="assets/config/defaults/runtime.json5"),  # Runtime defaults
            # A "view provider" that reads from global (read-only)
            ViewProvider(globalConfig),   # Inhering global values as a lower layer
            FileProvider(path=str(savePath), readOnly=False),
            RuntimeProvider(),
        ]
        return ConfigStore(namespace="config:runtime", validator=validator, providers=providers)

    def addRecentSave(self, entry: dict[str, Any], keep: int = 10):
        self.recentSaves.insert(0, entry)
        if len(self.recentSaves) > keep:
            del self.recentSaves[keep:]
