# file: backend/llm/__init__.py ; version: 1
"""LLM provider clients."""
from __future__ import annotations

__all__ = ["FreshContext", "ReuseContext", "LlmConversationSession", "LlmRetainedContextHandle"]


def __getattr__(name):
    """Exports owner-facing request/handle types without creating an eager import cycle."""
    if name in __all__:
        from backend.llm import executionSessions
        return getattr(executionSessions, name)
    raise AttributeError(name)
