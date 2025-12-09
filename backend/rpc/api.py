# backend/rpc/api.py
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

__all__ = [
    "exposeCapability", "listCapabilities", "getCapability",
    "routeRequest", "routeEmit", "routeSubscribe",
    "registerCapability", "unregisterCapability", "resetCapabilityInstance",
    "registerCapabilityInstance", "CapabilityFactory", "ICallContext",
    "IEmitContext", "ISubscribeContext", "ActiveSubscription",
]

logger = logging.getLogger(__name__)



_CAPS: dict[str, "CapabilityFactory"] = {}



def exposeCapability(name: str):
    """
    Decorator to register a capability class under a name like 'llm.pipeline@1'.
    The class may define: async def call(...), def emit(...), def subscribe(...)
    """
    def deco(cls):
        registerCapability(name, cls=cls, replace=False)
        return cls
    return deco



def listCapabilities() -> list[str]:
    return sorted(_CAPS.keys())



def getCapability(name: str):
    factory = _CAPS.get(name)
    if not factory:
        return None
    return factory.getInstance()



async def routeRequest(capability: str, path: str, args: list[Any] | None, ctx: "ICallContext") -> Any:
    if not isinstance(capability, str) or not capability:
        raise ValueError("capability must be a non-empty string")
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if args is not None and not isinstance(args, (list, tuple)):
        raise TypeError("args must be a list/tuple or None")
    
    cap = getCapability(capability)
    if not cap or not callable(getattr(cap, "call", None)):
        raise ValueError(f"Capability '{capability}' has no callable call() method")
    
    try:
        argv = tuple(args) if args is not None else []
        res = cap.call(path, argv, ctx)
    except Exception:
        # Let capability errors bubble up; higher layers may map to user-facing error frames.
        raise
    if inspect.isawaitable(res):
        return await res
    return res



def routeEmit(capability: str, path: str, payload: dict[str, Any] | None, ctx: "IEmitContext") -> None:
    if not isinstance(capability, str) or not capability:
        return
    if not isinstance(path, str) or not path:
        logger.debug("routeEmit: empty path for capability '%s'", capability)
        return
    
    cap = getCapability(capability)
    if not cap or not callable(getattr(cap, "emit", None)):
        # Emits are non-critical. Log at debug and return.
        logger.debug("routeEmit: capability '%s' has no callable emit() method", capability)
        return
    
    try:
        res = cap.emit(path, payload or {}, ctx)
        if inspect.isawaitable(res):
            async def _runner(awaitable):
                try:
                    await awaitable
                except Exception:
                    # Let the done-callback log; avoid double-logging here.
                    raise
            
            task = asyncio.create_task(_runner(res)) # Fire-and-forget
            # Avoid "Task exception was never retrieved"
            def _swallowExc(t: asyncio.Task) -> None:
                try:
                    exc = t.exception()
                    if exc is not None:
                        logger.debug("routeEmit task exception for '%s' path '%s': %r", capability, path, exc)
                except Exception:
                    # Nothing to do. Keep router robust.
                    pass
            task.add_done_callback(_swallowExc)
    except Exception:
        # Swallow emit errors to avoid crashing the caller path.
        logger.debug("routeEmit: error in capability '%s' emit() on path '%s'", capability, path, exc_info=True)
        return



async def routeSubscribe(
    capability: str,
    path: str,
    payload: dict[str, Any] | None,
    ctx: "ISubscribeContext"
) -> "ActiveSubscription":
    if not isinstance(capability, str) or not capability:
        raise ValueError("capability must be non-empty string")
    if not isinstance(path, str) or not path:
        raise ValueError("path must be non-empty string")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("payload must be dict or None")
    
    cap = getCapability(capability)
    if not cap or not callable(getattr(cap, "subscribe", None)):
        raise ValueError(f"Capability '{capability}' has no callable subscribe() method")
    
    # Capability returns a stream desc: {initial?, push?, onCancel?}
    try:
        desc = cap.subscribe(path, payload or {}, ctx)
    except Exception:
        raise
    if inspect.isawaitable(desc):
        desc = await desc
    
    # If we have a prebuilt ActiveSubscription, return it right away.
    if isinstance(desc, ActiveSubscription):
        return desc
    
    if not isinstance(desc, dict):
        raise TypeError("subscribe() must return a dict descriptor or ActiveSubscription")
    
    pushValue = desc.get("push")
    if pushValue is None:
        def _defaultPush(ev: dict[str, Any]) -> None:
            try:
                ctx.push(ev)
            except Exception:
                # Never let push errors bubble out or router.
                logger.debug("routeSubscribe: ctx.push raised", exc_info=True)
        pushFn: PushFn = _defaultPush
    else:
        if not callable(pushValue):
            raise TypeError("subscribe() descriptor 'push' must be callable if provided")
        # Wrap to harder 3rd-party push callables.
        rawPush: PushFn = cast(PushFn, pushValue)
        def _safePush(ev: dict[str, Any]) -> None:
            try:
                rawPush(ev)
            except Exception:
                logger.debug("routeSubscribe: custom push raised", exc_info=True)
        pushFn = _safePush
    
    onCancelValue = desc.get("onCancel")
    if onCancelValue is None:
        def _noop() -> None:
            return None
        onCancel: OnCancelFn = _noop
    else:
        if not callable(onCancelValue):
            raise TypeError("subscribe() descriptor 'onCancel' must be callable if provided")
        onCancel: OnCancelFn = cast(OnCancelFn, onCancelValue)
    
    initial = desc.get("initial") if "initial" in desc else None
    if initial is not None and not isinstance(initial, dict):
        raise TypeError("subscribe() descriptor 'initial' must be a dict if provided")
    return ActiveSubscription(push=pushFn, onCancel=onCancel, initial=initial)

# ------------------------------------------------------------------ #
# Internals
# ------------------------------------------------------------------ #

def registerCapability(
    name: str,
    *,
    cls: type | None = None,
    provider: Callable[[], Any] | None = None,
    replace: bool = False,
) -> None:
    """
    Registers or re-registers a capability by name.
    - Pass exactly one of `cls` or `provider`.
    - If replace=False and name exists, raises RuntimeError.
    - Replacing clears the existing singleton so new calls use the new binding.
    """
    if (cls is None) == (provider is None):
        raise ValueError("registerCapability: provide exactly one of cls or provider")
    if name in _CAPS and not replace:
        raise RuntimeError(f"Capability already registered: {name}")
    factory = CapabilityFactory(name=name, cls=cls, provider=provider, _singleton=None)
    _CAPS[name] = factory
    logger.debug("Register capability '%s' (replace=%s)", name, replace)



def unregisterCapability(name: str) -> bool:
    """
    Removes a capability entirely (no-op if missing). Returns True if removed.
    Existing instances already handed out keep working. New calls will fail until re-registered.
    """
    removed = _CAPS.pop(name, None) is not None
    if removed:
        logger.debug("Unregistered capability '%s'", name)
    return removed



def resetCapabilityInstance(name: str) -> bool:
    """
    Drops the cached singleton so the next getInstance() recreates it.
    Useful after config changes without rebuilding the factory binding.
    """
    factory = _CAPS.get(name)
    if not factory:
        return False
    factory._singleton = None
    logger.debug("Reset capability instance for '%s'", name)
    return True



def registerCapabilityInstance(name: str, instance: Any, *, replace: bool = True) -> None:
    """
    Binds a prebuilt instance as the singleton provider.
    This is convenient for objects that need constructor args or closures.
    """
    def _prov(instance=instance):
        return instance
    registerCapability(name, provider=_prov, replace=replace)



@dataclass
class CapabilityFactory:
    name: str
    cls: type | None = None
    provider: Callable[[], Any] | None = None
    _singleton: Any | None = None
    
    def getInstance(self) -> Any:
        # Lazily create once. Reuse thereafter.
        if self._singleton is not None:
            return self._singleton
        
        try:
            if self.provider is not None:
                self._singleton = self.provider()
            elif self.cls is not None:
                self._singleton = self.cls()
            else:
                raise RuntimeError(f"Capability '{self.name}' has no cls/provider")
        except Exception:
            # Make factory failures visible early with context.
            logger.exception("Failed to instantiate capability '%s'", self.name)
            raise
        
        return self._singleton



class ICallContext(Protocol):
    id: str
    origin: dict[str, Any] | None



class IEmitContext(Protocol):
    id: str
    origin: dict[str, Any] | None



class ISubscribeContext(Protocol):
    id: str
    origin: dict[str, Any] | None
    signal: asyncio.Event
    def push(self, payload: dict[str, Any]) -> None: ...



class PushFn(Protocol):
    def __call__(self, event: dict[str, Any], /) -> None: ...



class OnCancelFn(Protocol):
    def __call__(self) -> None: ...



@dataclass
class ActiveSubscription:
    push: PushFn
    onCancel: OnCancelFn
    initial: dict[str, Any] | None

    def __post_init__(self) -> None:
        if not callable(self.push):
            raise TypeError("ActiveSubscription.push must be a callable")
        if not callable(self.onCancel):
            raise TypeError("ActiveSubscription.onCancel must be a callable")
        if self.initial is not None and not isinstance(self.initial, dict):
            raise TypeError("ActiveSubscription.initial must be a dict or None")
