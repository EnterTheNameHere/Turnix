# backend/config/service.py
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Final

from backend.core.schema_registry import SchemaRegistry
from backend.config.store import ConfigStore
from backend.config.providers import DefaultsProvider, FileProvider, RuntimeProvider, ViewProvider
from backend.config.schema_loader import loadConfigSchemas
from backend.content.roots import ROOT_DIR

logger = logging.getLogger(__name__)

ASSETS_DEFAULTS_DIR = ROOT_DIR / "first-party" / "config" / "defaults"
USERDATA_CFG        = ROOT_DIR / "userdata" / "config"
SCHEMAS_DIR         = ROOT_DIR / "first-party" / "config" / "schema"



@dataclass
class ConfigService:
    registry: SchemaRegistry
    globalStore: ConfigStore

    @classmethod
    def bootstrap(cls) -> "ConfigService":
        registry = SchemaRegistry()
        # Register config schemas (e.g., config:global, config:realm, config:mod)
        loaded = loadConfigSchemas(registry, SCHEMAS_DIR)
        if loaded == 0:
            logger.warning("No config schemas loaded.")

        globalValidator = registry.getValidator("config", "global")
        globalStore = ConfigStore(
            namespace="config:global",
            validator=globalValidator,
            providers=[
                DefaultsProvider(path=str(ASSETS_DEFAULTS_DIR / "global.json5")),
                FileProvider(path=str(USERDATA_CFG / "global.json5"), readOnly=False),
                RuntimeProvider(),
            ],
        )
        return cls(registry=registry, globalStore=globalStore)

    def makeRealmStore(self, *, realmId: str) -> ConfigStore:
        realmValidator = self.registry.getValidator("config", "realm")
        savePath: Final = ROOT_DIR / "saves" / realmId / "config.json5"
        return ConfigStore(
            namespace=f"config:realm:{realmId}",
            validator=realmValidator,
            providers=[
                DefaultsProvider(path=str(ASSETS_DEFAULTS_DIR / "realm.json5")),
                ViewProvider(self.globalStore),
                FileProvider(path=str(savePath), readOnly=False),
                RuntimeProvider(),
            ],
        )
