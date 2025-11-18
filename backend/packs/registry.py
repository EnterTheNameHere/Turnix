# backend/packs/registry.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from backend.content.packs import PackResolver, ResolvedPack
from backend.packs.loaders import (
    AppPackLoader,
    ContentPackLoader,
    LoadedPack,
    ModPackLoader,
    PackLoadContext,
    PackLoader,
    ViewPackLoader,
)

__all__ = [
    "PackRegistry",
]


@dataclass
class PackRegistry:
    """
    High-level orchestrator that discovers pack manifests and hands them to the
    appropriate loader implementation.
    """

    resolver: PackResolver = field(default_factory=PackResolver)

    def __post_init__(self) -> None:
        self._loaders: dict[str, PackLoader] = {}
        self.registerLoader(AppPackLoader())
        self.registerLoader(ViewPackLoader())
        self.registerLoader(ContentPackLoader())
        self.registerLoader(ModPackLoader())

    # ----- Loader registry -----

    def registerLoader(self, loader: PackLoader) -> None:
        self._loaders[loader.kind] = loader

    def loaderFor(self, kind: str) -> PackLoader:
        if kind not in self._loaders:
            raise KeyError(f"No loader registered for kind '{kind}'")
        return self._loaders[kind]

    # ----- Discovery helpers -----

    def discover(
        self,
        *,
        kinds: set[str] | None = None,
        overrides: Mapping[str, Iterable[Path]] | None = None,
    ) -> list[ResolvedPack]:
        return self.resolver.listPacks(kinds=kinds, overrides=overrides)

    # ----- Loading -----

    def loadByQualifiedId(
        self,
        qidOrId: str,
        *,
        kind: str,
        overrides: Mapping[str, Iterable[Path]] | None = None,
        context: PackLoadContext | None = None,
    ) -> LoadedPack:
        resolved = self.resolver.resolvePack(qidOrId, kinds={kind}, overrides=overrides)
        if resolved is None:
            raise LookupError(f"Pack '{qidOrId}' ({kind}) not found")
        return self.loadResolved(resolved, context=context)

    def loadResolved(
        self,
        resolved: ResolvedPack,
        *,
        context: PackLoadContext | None = None,
    ) -> LoadedPack:
        loader = self.loaderFor(resolved.kind)
        return loader.load(resolved, context=context)
