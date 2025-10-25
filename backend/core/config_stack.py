# backend/core/config_stack.py
from __future__ import annotations
from typing import Any, Literal, cast
from collections.abc import Mapping
from dataclasses import dataclass, field
import copy

__all__ = [
    "MergeStrategy", "mergeWithStrategy", "ConfigLayer",
    "ConfigStack", "ConfigView",
]



MergeStrategy = Literal["deep", "replace", "append", "prepend", "uniqueAppend"]
_ALL_STRATEGIES: tuple[str, ...] = ("deep", "replace", "append", "prepend", "uniqueAppend")
_LIST_STRATEGIES: tuple[str, ...] = ("replace", "append", "prepend", "uniqueAppend")



def mergeWithStrategy(left: Any, right: Any) -> Any:
    """
    Deep merge with an optional per-object directive:
      - dicts: if right has __merge, apply behavior:
        "deep" (default): recurse on dicts, replace other types
        "replace": replace left entirely with right (minus __merge)
      - lists: use __merge in a sibling dict e.g. {"listKey": [...], "listKey__merge": "append"}
        or wrap list in {"__value": [...], "__merge": "..."} if you need to carry a directive
      - scalars: right replaces left
    """
    # Top-level list wrapper support: {"__value": [...], "__merge": "..."}
    # Only valid when the left side is a list, and only when the mapping is a *pure* wrapper.
    if isinstance(right, Mapping) and "__value" in right and "__merge" in right:
        # Treat as wrapper only if no extra keys are present.
        if set(right.keys()) <= {"__value", "__merge"} and isinstance(right.get("__value"), list):
            if not isinstance(left, list):
                # If left isn't a list, this wrapper is illegal at this path.
                raise TypeError(
                    'List wrapper {"__value": [...], "__merge": ...} used where the target is not a list. '
                    'Place the wrapper under a key that holds a list.'
                )        
            listRight, ok = _extractListValue(right)
            if ok:
                strategy: MergeStrategy = cast(MergeStrategy, right.get("__merge", "replace"))
                _validateMergeStrategy(strategy, context="top-level list", listContext=True)
                return _mergeLists(left, listRight, strategy)
    # Dicts
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        strategy: MergeStrategy = cast(MergeStrategy, right.get("__merge", "deep"))
        _validateMergeStrategy(strategy, context="object", listContext=False)
        if strategy == "replace":
            # Drop control key
            out = {key: copy.deepcopy(value) for key, value in right.items() if key != "__merge"}
            return out
        # Reserved key guard: reject stray "*__merge" helpers that don't pair with a base key.
        for key in right:
            if key.endswith("__merge") and key != "__merge":
                base = key[:-len("__merge")]
                if base not in right:
                    raise ValueError(
                        f'Unexpected reserved key "{key}" without matching base key "{base}". '
                        'Place merge directives either as "<key>__merge" next to <key>, '
                        'or use the wrapper {"__value": [...], "__merge": "..."} under the key.'
                    )
        # Deep
        out: dict[str, Any] = {**left}
        for key, rightValue in right.items():
            if key == "__merge":
                continue
            # Never propagate side-channel keys like "<key>__merge" into output.
            if key.endswith("__merge"):
                continue
            lv = out.get(key)
            # List strategy via side-channel key: "<key>__merge"
            listStrategyKey = f"{key}__merge"
            if listStrategyKey in right:
                # Only honor the directive if the value is a list (or proper wrapper).
                listRight, ok = _extractListValue(rightValue)
                if ok:
                    mergeStrategy: MergeStrategy = cast(MergeStrategy, right[listStrategyKey])
                    _validateMergeStrategy(mergeStrategy, context=f'key "{key}"', listContext=True)
                    out[key] = _mergeLists(lv, listRight, mergeStrategy)
                    continue
                # Otherwise ignore the side-channel and fall through to normal handling
            # Support wrapper form:
            if isinstance(rightValue, Mapping) and "__value" in rightValue and "__merge" in rightValue:
                mergeStrategy = cast(MergeStrategy, rightValue["__merge"])
                _validateMergeStrategy(mergeStrategy, context=f'key "{key}"', listContext=True)
                listRight, ok = _extractListValue(rightValue)
                if not ok:
                    raise ValueError(f'List wrapper for "{key}" must contain list under "__value"')
                out[key] = _mergeLists(lv, listRight, mergeStrategy)
                continue
            if isinstance(lv, Mapping) and isinstance(rightValue, Mapping):
                out[key] = mergeWithStrategy(lv, rightValue)
            elif isinstance(lv, list) and isinstance(rightValue, list):
                out[key] = _mergeLists(lv, rightValue, "replace") # Default lists replace unless keyed
            else:
                out[key] = copy.deepcopy(rightValue)
        # Final sweep: ensure no stray "*__merge" helper keys remain.
        for key in list(out.keys()):
            if key.endswith("__merge"):
                out.pop(key, None)
        return out
    # Lists
    if isinstance(left, list) and isinstance(right, list):
        return list(right) # Default: replace (avoid aliasing)
    # Scalars
    return copy.deepcopy(right)



def _mergeLists(left: Any, right: Any, strategy: MergeStrategy) -> Any:
    left = list(left or [])
    right = list(right or [])
    if strategy == "replace":
        return right
    if strategy == "append":
        return left + right
    if strategy == "prepend":
        return right + left
    if strategy == "uniqueAppend":
        # Be robust to unhashable items (dicts, lists). Fall back to O(n^2) equality check.
        out = left[:]
        try:
            seen = set(left)
            for item in right:
                if item not in seen:
                    out.append(item)
                    seen.add(item)
            return out
        except TypeError:
            for item in right:
                if not any(item == existing for existing in out):
                    out.append(item)
            return out
    # Default: deep-ish replace
    return right



def _extractListValue(v: Any) -> tuple[list[Any], bool]:
    """Support raw list or wrapper {"__value": list, "__merge": ...}."""
    if isinstance(v, list):
        return v, True
    if isinstance(v, Mapping) and "__value" in v and isinstance(v["__value"], list):
        return list(v["__value"]), True
    return [], False



def _validateMergeStrategy(strategy: str, *, context: str, listContext: bool = False) -> None:
    allowed = _LIST_STRATEGIES if listContext else _ALL_STRATEGIES
    if strategy not in allowed:
        raise ValueError(f"Invalid __merge='{strategy}' in {context}; allowed: {', '.join(allowed)}")



@dataclass(frozen=True)
class ConfigLayer:
    """
    One immutable configuration layer.
    - name: human readable
    - scope: "global" | "modpack" | "game" | "mod" | "asset" | "runtime"
    - data: plain JSON-like dict
    - tags: optional qualifiers (e.g., modId, assetId)
    """
    name: str
    scope: Literal["global", "modpack", "game", "mod", "asset", "runtime"]
    data: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)



class ConfigStack:
    """
    An ordered set of layers with fixed precedence.
    Create one per GameRealm (for game/modpack layers) and optionally
    per Session for assets/mod-runtime overlays.
    """
    def __init__(self):
        self._layers: list[ConfigLayer] = []
        self._version: int = 0
    
    def setLayers(self, layers: list[ConfigLayer]) -> None:
        # Replace entire stack atomically
        self._layers = list(layers)
        self._version += 1
    
    def addLayer(self, layer: ConfigLayer) -> None:
        self._layers.append(layer)
        self._version += 1

    def removeLayer(self, name: str) -> bool:
        idx = next((index for index, layer in enumerate(self._layers) if layer.name == name), -1)
        if idx >= 0:
            del self._layers[idx]
            self._version += 1
            return True
        return False
    
    def layers(self) -> list[ConfigLayer]:
        return list(self._layers)
    
    def version(self) -> int:
        return self._version

    def view(self, *, modId: str | None = None, assetId: str | None = None) -> ConfigView:
        return ConfigView(stack=self, modId=modId, assetId=assetId)

    def _overlayName(self, modId: str | None, assetId: str | None) -> str:
        return f'overlay:{modId or "_"}:{assetId or "_"}'

    def _ensureOverlay(self, *, modId: str | None, assetId: str | None) -> None:
        name = self._overlayName(modId, assetId)
        for layer in self._layers:
            if layer.name == name:
                return
        self._layers.append(ConfigLayer(
            name=name,
            scope="runtime",
            data={},
            tags={**({"modId": modId} if modId else {}), **({"assetId": assetId} if assetId else {})}
        ))
        self._version += 1
    
    def setOverlay(self, *, modId: str | None, assetId: str | None, path: str, value: Any) -> None:
        """Set a temporary runtime override at path for this (modId, assetId) context."""
        from backend.core.dictpath import setByPath
        self._ensureOverlay(modId=modId, assetId=assetId)
        name = self._overlayName(modId, assetId)
        # Replace the whole layer immutably (ConfigLayer is frozen)
        newLayers: list[ConfigLayer] = []
        for layer in self._layers:
            if layer.name != name:
                newLayers.append(layer)
                continue
            data = dict(layer.data)
            setByPath(data, path, value)
            newLayers.append(ConfigLayer(name=layer.name, scope=layer.scope, data=data, tags=layer.tags))
        self._layers = newLayers
        self._version += 1
    
    def setOverlayMany(self, *, modId: str | None, assetId: str | None, entries: dict[str, Any]) -> None:
        """Batch set multiple paths into the overlay."""
        from backend.core.dictpath import setByPath
        if not entries:
            return
        self._ensureOverlay(modId=modId, assetId=assetId)
        name = self._overlayName(modId, assetId)
        newLayers: list[ConfigLayer] = []
        for layer in self._layers:
            if layer.name != name:
                newLayers.append(layer)
                continue
            data = dict(layer.data)
            for path, value in entries.items():
                setByPath(data, path, value)
            newLayers.append(ConfigLayer(name=layer.name, scope=layer.scope, data=data, tags=layer.tags))
        self._layers = newLayers
        self._version += 1
    
    def clearOverlayMany(self, *, modId: str | None, assetId: str | None, path: str | None = None) -> None:
        """Clear entire overlay or a single path."""
        from backend.core.dictpath import deleteByPath
        name = self._overlayName(modId, assetId)
        idx = next((index for index, layer in enumerate(self._layers) if layer.name == name), -1)
        if idx < 0:
            return
        if path is None:
            del self._layers[idx]
            self._version += 1
            return
        # Remove only the path (recreate layer immutably)
        layer = self._layers[idx]
        data = dict(layer.data)
        deleteByPath(data, path)
        self._layers[idx] = ConfigLayer(name=layer.name, scope=layer.scope, data=data, tags=layer.tags)
        self._version += 1



class ConfigView:
    """
    A filtered, merged view on a ConfigStack for a given context (e.g., modId, assetId).
    """
    def __init__(self, *, stack: ConfigStack, modId: str | None, assetId: str | None) -> None:
        self._stack = stack
        self._modId = modId
        self._assetId = assetId
        self._effective: dict[str, Any] | None = None
        self._effectiveVersion: int = -1

    def _eligible(self, layer: ConfigLayer) -> bool:
        # If layer is tagged with modId/assetId, it only applies when matching.
        tags = layer.tags
        if "modId" in tags and tags["modId"] != self._modId:
            return False
        if "assetId" in tags and tags["assetId"] != self._assetId:
            return False
        return True
    
    def effective(self) -> dict[str, Any]:
        # Recompute if cache is empty or stack has changed.
        if self._effective is not None and self._effectiveVersion == self._stack.version():
            return self._effective
        merged: dict[str, Any] = {}
        # Precedence: global → modpack → game → mod → asset → runtime
        order = ["global", "modpack", "game", "mod", "asset", "runtime"]
        for scope in order:
            for layer in self._stack.layers():
                if layer.scope != scope:
                    continue
                if not self._eligible(layer):
                    continue
                merged = mergeWithStrategy(merged, layer.data)
                if not isinstance(merged, dict):
                    raise TypeError(
                        f'Layer "{layer.name}" (scope="{layer.scope}") produced non-dict at root. '
                        'Configs must remain object-shaped at the top level. '
                        'If you intended to merge a list, use the wrapper under a key, e.g. '
                        '{"myList": {"__value": [...], "__merge": "append"}}.'
                    )
        self._effective = merged
        self._effectiveVersion = self._stack.version()
        return merged
    
    def get(self, path: str, default: Any = None) -> Any:
        from backend.core.dictpath import getByPath
        val = getByPath(self.effective(), path)
        return default if val is None else val

    def set(self, path: str, value: Any) -> None:
        """Set a temporary override in the runtime overlay for this view (mod/asset)."""
        self._stack.setOverlay(modId=self._modId, assetId=self._assetId, path=path, value=value)
    
    def setMany(self, entries: dict[str, Any]) -> None:
        self._stack.setOverlayMany(modId=self._modId, assetId=self._assetId, entries=entries)
    
    def clear(self, path: str | None = None) -> None:
        self._stack.clearOverlayMany(modId=self._modId, assetId=self._assetId, path=path)
