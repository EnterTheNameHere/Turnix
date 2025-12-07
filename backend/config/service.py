# backend/config/service.py
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Final

from backend.app.globals import getContentRootsService
from backend.config.providers import DefaultsProvider, FileProvider, OverrideProvider, ViewProvider
from backend.config.schema_loader import loadConfigSchemas
from backend.config.store import ConfigStore
from backend.content.pack_descriptor import PackDescriptor
from backend.content.packs import ResolvedPack
from backend.content.saves import SaveManager
from backend.core.schema_registry import SchemaRegistry
from backend.content.content_roots import ROOT_DIR

logger = logging.getLogger(__name__)

ASSETS_DEFAULTS_DIR = ROOT_DIR / "first-party" / "config" / "defaults"
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
        
        # User config lives under the selected userdata root:
        #     <userdata-root>/config/global.json5
        roots = getContentRootsService()
        userdataBase = roots.getWriteDir("userdata") # Creates <base>/userdata if missing
        userdataCfgDir = userdataBase / "config"
        try:
            userdataCfgDir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If directory creation fails, FileProvider will surface the error on save
            logger.exception("Failed to create userdata config directory at '%s'", userdataCfgDir)
        
        globalStore = ConfigStore(
            namespace="config:global",
            validator=globalValidator,
            providers=[
                DefaultsProvider(path=str(ASSETS_DEFAULTS_DIR / "global.json5")),
                FileProvider(path=str(userdataCfgDir / "global.json5"), readOnly=False),
                OverrideProvider(),
            ],
        )
        return cls(registry=registry, globalStore=globalStore)

    def makeAppInstanceStore(self, *, appPack: ResolvedPack, appInstanceId: str) -> ConfigStore:
        validator = self.registry.getValidator("config", "appInstance")
        roots = getContentRootsService()
        
        # TODO(runtime-config):
        #    This is a temporary, minimal implementation.
        #    In the future, appInstance defaults should be built from a
        #    fully resolved pack graph (appPack + viewPacks + contentPacks + mods),
        #    collecting and merging all pack-level default config trees into a
        #    single flattened snapshot that is stored with the appInstance.
        
        savesBase = roots.getWriteDir("saves")
        appKey = SaveManager().appIdToKey(appPack.id)
        saveDir = savesBase / appKey / appInstanceId
        try:
            saveDir.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.exception(
                "Failed to create directory for appPack/appInstance '%s/%s' at '%s' to store config",
                appPack.id,
                appInstanceId,
                saveDir,
            )
        
        return ConfigStore(
            namespace=f"config:appInstance:{appPack.id}:{appInstanceId}",
            validator=validator,
            providers=[
                DefaultsProvider(path=str(appPack.rootDir / "config.json5")),
                ViewProvider(self.globalStore),
                FileProvider(path=str(saveDir / "config.json5")),
                OverrideProvider(),
            ],
        )

    def makePackStore(self, *, pack: ResolvedPack) -> ConfigStore:
        validator = self.registry.getValidator("config", f"pack:{pack.kind}")
        roots = getContentRootsService()
        
        userdataBase = roots.getWriteDir("userdata")
        packConfigDir: Final = userdataBase / "config" / pack.kind
        try:
            packConfigDir.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.exception(
                "Failed to create directory for pack='%s' at '%s' to store config",
                pack.id,
                str(packConfigDir),
            )
        
        
        # TODO(pack-config):
        #    This is a temporary heuristic for pack-level defaults.
        #    Later, we should support a richer discovery scheme, e.g.:
        #      - pack.rootDir / "config.json5"
        #      - pack.rootDir / "config/defaults.json5"
        #      - pack.rootDir / "config/<kind>.json5" or similar
        #    and possibly allow packs to declare where their config lives
        #    in the manifest, so we do not hard-code file names here.
        packDefaultConfigFile = pack.rootDir / "config.json5"
        defaultsFilePath = packDefaultConfigFile if packDefaultConfigFile.is_file() else None
        
        providers = []
        if defaultsFilePath:
            providers.append(DefaultsProvider(path=str(defaultsFilePath)))
        providers.append(ViewProvider(self.globalStore))
        providers.append(FileProvider(path=str(packConfigDir / f"{pack.id}.json5"), readOnly=False))
        providers.append(OverrideProvider())
        
        return ConfigStore(
            namespace=f"config:pack:{pack.kind}:{pack.id}",
            validator=validator,
            providers=providers,
        )

    # ------------------------------------------------------------------ #
    # Pack-aware schema + config loading
    # ------------------------------------------------------------------ #
    
    def registerSchemasFor(self, packs: list[PackDescriptor]) -> None:
        """
        Register config schemas shipped by the given packs.
        
        Intended usage:
            - After PackManager has selected the active set of packs for
              this run (app, view, mods, system packs).
            - Before any defaults/user config are loaded.
        
        Implementation sketch (to be filled in later):
            - For each pack, look for schema files under known relative
              paths (for example "config/schema/**/*.schema.json")
            - Load and register them into self.registry.
        """
        # TODO: Implement schema discovery inside packs
        return
    
    def loadConfigFor(self, packs: list[PackDescriptor]) -> None:
        """
        Load default and user configuration for the given packs.
        
        Intended usage:
            - After registerSchemasFor(packs) has been called.
            - Before packs are considered "prepared" and before mod
              onLoad() is invoked.
        
        Implementation sketch (to be filled in later):
            - For each pack, merge:
                1. defaults from pack (config/defaults/*.json5)
                2. user config from <userdata>/ (per-pack/app files)
            - Validate with self.registry and apply into self.store.
        """
        # TODO: Implement pack-scoped config loading.
        return
