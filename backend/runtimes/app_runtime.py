# backend/game/app_runtime.py
from __future__ import annotations
from pathlib import Path
from typing import Any

from backend.app.paths import ROOT_DIR
from backend.config.providers import DefaultsProvider, FileProvider, RuntimeProvider, ViewProvider
from backend.config.service import ConfigService
from backend.config.store import ConfigStore
from backend.core.schema_registry import SchemaRegistry
from backend.runtimes.instance import RuntimeInstance

__all__ = ["AppRuntime"]



class AppRuntime(RuntimeInstance):
    """
    Authoritative runtime container.
    - Owns the main Session (global timeline)
    - Can create additional sessions (hidden/temp), optionally tagged with ownerViewId
    - View *attach to* sessions; they don't own main one.
    """
    from backend.memory.memory_layer import MemoryLayer
    
    def __init__(
        self,
        *,
        runtimeId: str | None = None,
        configService: ConfigService,
        kernelMemoryLayers: list[MemoryLayer] | None = None,
        configRegistry: SchemaRegistry,
        globalConfigView: ConfigStore,
        saveRoot: Path | None = None,
    ) -> None:
        from backend.sessions.session import Session

        super().__init__(runtimeId=runtimeId, kernelMemoryLayers=kernelMemoryLayers, saveRoot=saveRoot)
        self.version: int = 0

        # Config specific to this runtime
        self.config = self._initConfig(configRegistry, globalConfigView)
        self.configService = configService
        self.globalConfig: ConfigStore = self.configService.globalStore

        # Create the main (world-authoritative) session up-front.
        self.mainSession: Session = self.makeSession(kind="main")
    
    def _initConfig(self, reg: SchemaRegistry, globalConfig: ConfigStore) -> ConfigStore:
        validator = reg.getValidator("config", "runtime")
        savePath = ROOT_DIR / "saves" / self.id / "config.json5"
        providers = [
            DefaultsProvider(path=str(ROOT_DIR / "assets" / "config" / "defaults" / "runtime.json5")),  # Runtime defaults
            # A "view provider" that reads from global (read-only)
            ViewProvider(globalConfig),   # Inhering global values as a lower layer
            FileProvider(path=str(savePath), readOnly=False),
            RuntimeProvider(),
        ]
        return ConfigStore(namespace="config:runtime", validator=validator, providers=providers)

    def snapshot(self) -> dict[str, object]:
        return {**super().snapshot(), "mainSessionId": self.mainSession.id}

    def destroySession(self, sessionId: str) -> dict[str, Any]:
        if sessionId == self.mainSession.id:
            raise ValueError("Cannot destroy main session")
        return super().destroySession(sessionId)
