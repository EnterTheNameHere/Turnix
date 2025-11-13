# backend/app/globals.py
from __future__ import annotations
from typing import Any, cast, TYPE_CHECKING

from backend.app.context import PROCESS_REGISTRY
from backend.core.dictpath import getByPath
from backend.core.errors import ReactorScramError

if TYPE_CHECKING:
    from backend.kernel import Kernel
    from backend.runtimes.instance import RuntimeInstance
    from backend.config.service import ConfigService
    from backend.core.permissions import PermissionManager
    from backend.sessions.session import Session
    from backend.content.roots import RootsService



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



def getActiveRuntime() -> RuntimeInstance:
    runtime = PROCESS_REGISTRY.get("runtime.active")
    if runtime is None:
        raise ReactorScramError(
            "Active runtime is None.\n"
            "⚠️ RUNTIME MISSING ⚠️\n"
            "Turnix looked for the current runtime and found philosophical emptiness.\n"
            "This slot should contain: main menu, game, or literally any runtime.\n"
        )
    return cast("RuntimeInstance", runtime)



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



def getRootsService() -> RootsService:
    roots = PROCESS_REGISTRY.get("roots.service")
    if roots is None:
        raise ReactorScramError(
            "RootsService is None.\n"
            "⚠️ ROOTS SERVICE MISSING ⚠️\n"
            "Oh dear.\n"
            "It appears the RootsService has not been set up.\n"
            "One could carry on, but that would be... ill-advised.\n"
            "Kettle’s going on. Tea?"
        )
    return cast("RootsService", roots)



def getMainSessionOrScram() -> Session:
    """
    returns active Runtime's main Session. It raises if no main session exists.
    """
    runtime = getActiveRuntime()
    if runtime is not None and runtime.mainSession is not None:
        return runtime.mainSession
    
    # If we got here, the app state is corrupted and the UI cannot function.
    raise ReactorScramError(
        "Main session is None\n"
        "⚠️ MAIN SESSION MISSING ⚠️\n"
        "Runtime doesn't have a main session. View's purpose is to show UI. But it doesn't have a main session. "
        "We need UI to show the session. But we don't have None to UI. But Session is to show Runtime. "
        "UI needs Runtime to show Session. Runtime show UI. Session Runtime None. Main UI. Run."
        "(Some runtimes might not use main session. If you use such runtime, don't ask for main session.)"
    )



def config(path: str, default: Any = None) -> Any:
    """
    Read a dotted path from the merged global configuration.

    Uses a snapshot (nested dicts supported). Returns `default` when the path is not found.
    
    Example:
      value = config("timeouts.classes.request.fast.serviceTtlMs") # returns 800
      value = config("non.existing.path", 300)                     # returns 300
    """
    store = getConfigService().globalStore
    snap = store.snapshot()
    val = getByPath(snap, path)
    if val is None:
        val = getByPath(snap, "values." + path)
    return default if val is None else val



def configBool(path: str, default: bool = False) -> bool:
    """
    Read a boolean from the merged global configuration.
    """
    val = config(path, None)
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return bool(val)
