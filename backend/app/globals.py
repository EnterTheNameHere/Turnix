# backend/app/globals.py
from __future__ import annotations
from typing import TYPE_CHECKING, cast

from backend.app.context import PROCESS_REGISTRY
from backend.core.errors import ReactorScramError

if TYPE_CHECKING:
    from backend.kernel import Kernel
    from backend.runtimes.base import BaseRuntime
    from backend.config.service import ConfigService
    from backend.core.permissions import PermissionManager



def getKernel() -> Kernel:
    kernel = PROCESS_REGISTRY.get("kernel")
    if kernel is None:
        raise ReactorScramError(
            "Kernel check failed.\n"
            "⚠️ KERNEL MISSING ⚠️\n"
            "In that moment, Turnix stared into the void — and the void returned None. "
            "With a trembling hand, it pressed AZ-5.\n"
            "The world fell silent."
        )
    return cast("Kernel", kernel)



def getActiveRuntime() -> BaseRuntime:
    runtime = PROCESS_REGISTRY.get("runtime.active")
    if runtime is None:
        raise ReactorScramError(
            "Active runtime is None.\n"
            "⚠️ RUNTIME MISSING ⚠️\n"
            "Turnix looked for the current runtime and found philosophical emptiness.\n"
            "This slot should contain: main menu, game, or literally any runtime.\n"
        )
    return cast("BaseRuntime", runtime)



def getConfigService() -> ConfigService:
    cfg = PROCESS_REGISTRY.get("config.service")
    if cfg is None:
        raise ReactorScramError(
            "ConfigService is None.\n"
            "⚠️ CONFIG SERVICE MISSING ⚠️\n"
            "All sliders set to `???`.\n"
            "Please boot a ConfigService before touching buttons."
        )
    return cast("ConfigService", cfg)



def getPermissions() -> PermissionManager:
    perms = PROCESS_REGISTRY.get("permissions")
    if perms is None:
        raise ReactorScramError(
            "PermissionManager is None.\n"
            "⚠️ PERMISSION MANAGER MISSING ⚠️\n"
            "All authorization requests will be auto-denied until further notice.\n"
            "There are no rules. No laws. Only unchecked function calls."
        )
    return cast("PermissionManager", perms)
