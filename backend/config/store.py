# backend/config/store.py
from __future__ import annotations

from typing import Any, Callable, Literal

from .types import ConfigProvider, ChangeListener, ConfigStore as ConfigStoreProtocol, ConfigMeta



class ConfigStore(ConfigStoreProtocol):
    """
    Minimal layered config store:
      - read: first-hit from providers[0..n]
      - write: dispatch to a target provider (runtime/save/global)
      - validate: on set(), validate the *effective* merged document
    """

    TARGET_MAP = {
        "runtime": "runtime",
        "save": "save",
        "global": "global",
    }

    def __init__(self, *, namespace: str, validator, providers: list[ConfigProvider]):
        self.namespace = namespace
        self._validator = validator
        self._providers = providers
        self._listeners: list[ChangeListener] = []

        # Index providers by role (best-effort)
        self._roleIdx: dict[str, int] = {}
        for idx, provider in enumerate(self._providers):
            name = provider.__class__.__name__.lower()
            # Top override layer (what callers see as "runtime" target)
            if any(token in name for token in ("override", "runtime")) and "runtime" not in self._roleIdx:
                self._roleIdx["runtime"] = idx
            # Save-backed layer (file on disk)
            if any(token in name for token in ("file", "save")) and "save" not in self._roleIdx:
                self._roleIdx["save"] = idx
            # Global-ish base layer (defaults, view, dict-backed, etc.)
            if any(token in name for token in ("view", "global", "defaults", "dict")):
                self._roleIdx.setdefault("global", idx)
    
    # ----- Helpers -----

    def _resolveTargetIdx(self, target: Literal["runtime", "save", "global"]) -> int:
        if target not in self._roleIdx:
            raise KeyError(f"No provider mapped for target '{target}' in {self.namespace}")
        return self._roleIdx[target]
    
    def _merged(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        # We merge from bottom to top
        for provider in self._providers:
            merged.update(provider.to_dict())
        return merged
    
    def get(self, key: str) -> Any | None:
        for provider in reversed(self._providers): # Topmost first precedence
            value = provider.get(key)
            if value is not None:
                return value
        return None
    
    def set(
        self,
        key: str,
        value: Any,
        *,
        target: Literal["runtime", "save", "global"] = "runtime",
        actor: str = "system",
    ) -> None:
        oldValue = self.get(key)
        # Provisional write to the target layer
        idx = self._resolveTargetIdx(target)
        self._providers[idx].set(key, value)

        # Validate whole effective config after the write
        effective = self._merged()
        try:
            self._validator(effective)
        except Exception:
            # rollback
            if oldValue is None:
                # Delete key from target layer
                self._providers[idx].set(key, None)
            else:
                self._providers[idx].set(key, oldValue)
            raise

        # Notify listeners after successful validation
        newValue = self.get(key)
        if oldValue != newValue:
            context = {"namespace": self.namespace, "actor": actor, "target": target}
            for fn in list(self._listeners):
                try:
                    fn(key, oldValue, newValue, context)
                except Exception:
                    pass # Listeners should not break the store

    def meta(self, key: str) -> ConfigMeta:
        # Placeholder: without a schema-driven meta extractor, return sane defaults
        return {"visibility": "public", "mutable": "runtime", "summary": ""}
    
    def subscribe(self, fn: ChangeListener) -> Callable[[], None]:
        self._listeners.append(fn)
        def _unsub() -> None:
            try:
                self._listeners.remove(fn)
            except ValueError:
                pass
        return _unsub
    
    def snapshot(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "values": self._merged(),
            "layers": [provider.__class__.__name__ for provider in self._providers],
        }
    
    def diff(self, other: "ConfigStore") -> dict[str, tuple[Any, Any]]:
        first = self._merged()
        second = other._merged()
        keys = set(first.keys()) | set(second.keys())
        out: dict[str, tuple[Any, Any]] = {}
        for key in sorted(keys):
            if first.get(key) != second.get(key):
                out[key] = (first.get(key), second.get(key))
        return out
    
    def saveAll(self) -> None:
        # Convenience hook for UI or shutdown
        for provider in self._providers:
            try:
                provider.save()
            except Exception:
                pass
