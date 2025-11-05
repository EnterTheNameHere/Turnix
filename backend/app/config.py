# backend/app/config.py
from __future__ import annotations
import logging

from backend.app.context import PROCESS_REGISTRY
from backend.config.service import ConfigService
from backend.config.schema_loader import SchemaRegistry
from backend.config.store import ConfigStore

logger = logging.getLogger(__name__)

__all__ = [
    "initConfig", "getRegistry", "getGlobalConfig",
    "allowSymlinks", "pickBudgetMs", "resolveClassCfg",
]

# ------------------------------------------------------------------ #
# Module singletons
# ------------------------------------------------------------------ #

_CONFIG_SERVICE: ConfigService | None = None

# ------------------------------------------------------------------ #
# Core initialization
# ------------------------------------------------------------------ #

def initConfig() -> None:
    """
    Initialize config subsystem (idempotent).

    Uses ConfigService as the single source of truth.
    """
    global _CONFIG_SERVICE
    if _CONFIG_SERVICE is not None:
        # Already initialized
        return
    _CONFIG_SERVICE = ConfigService.bootstrap()
    PROCESS_REGISTRY.register("config.service", _CONFIG_SERVICE, overwrite=True)
    PROCESS_REGISTRY.register("config.global", _CONFIG_SERVICE.globalStore, overwrite=True)
    PROCESS_REGISTRY.register("config.registry", _CONFIG_SERVICE.registry, overwrite=True)
    logger.info("Config initialized (registry + global store ready)")



def _ensure() -> ConfigService:
    global _CONFIG_SERVICE
    if _CONFIG_SERVICE is None:
        initConfig()
    assert _CONFIG_SERVICE is not None
    return _CONFIG_SERVICE

def getRegistry() -> SchemaRegistry:
    """
    Returns the process-wide SchemaRegistry.
    """
    return _ensure().registry



def getGlobalConfig() -> ConfigStore:
    """
    Returns the global ConfigStore.
    """
    return _ensure().globalStore



def allowSymlinks() -> bool:
    from backend.app.globals import configBool
    return configBool("mods.allowSymlinks", False)



def pickBudgetMs(opts) -> int:
    if not isinstance(opts, dict):
        return 3000
    budgetMs = opts.get("budgetMs")
    return int(budgetMs or resolveClassCfg(opts).get("serviceTtlMs", 3000))



def resolveClassCfg(opts) -> dict:
    from backend.app.globals import config
    cls = opts.get("class") or "request.medium"
    classes = config("timeouts.classes", {})
    cfg = classes.get(cls) if isinstance(classes, dict) else None
    return cfg or {"serviceTtlMs": 3000, "clientPatienceExtraMs": 200}
