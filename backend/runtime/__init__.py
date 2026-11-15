# file: backend/runtime/__init__.py ; version: 5
"""Lazy host exports allow neutral runtime-domain imports without Application cycles."""

__all__ = ["RuntimeHost", "RuntimeHostState"]


def __getattr__(name):
    """Preserves host entry points without constructing the LLM/Application import graph."""
    if name in __all__:
        from backend.runtime.runtimeHost import RuntimeHost, RuntimeHostState
        return {"RuntimeHost": RuntimeHost, "RuntimeHostState": RuntimeHostState}[name]
    raise AttributeError(name)
