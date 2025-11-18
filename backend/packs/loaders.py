# backend/packs/loaders.py
from __future__ import annotations
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from backend.content.packs import PackManifest, ResolvedPack
from backend.runtimes.app_runtime import AppRuntime
from backend.config.service import ConfigService
from backend.config.store import ConfigStore
from backend.core.schema_registry import SchemaRegistry
from backend.memory.memory_layer import MemoryLayer

__all__ = [
    "PackLoadContext",
    "PackLoader",
    "LoadedPack",
    "LoadedAppPack",
    "LoadedViewPack",
    "LoadedContentPack",
    "LoadedModPack",
    "AppPackLoader",
    "ViewPackLoader",
    "ContentPackLoader",
    "ModPackLoader",
]


@dataclass(slots=True)
class PackLoadContext:
    """
    Optional context that loaders may use for dependency resolution.

    The overrides mapping typically comes from SavePack overrides so a loader can
    prioritize copies of packs that live inside saves/<app>/<instance>/packs.
    """

    overrides: Mapping[str, Iterable[Path]] | None = None
    extras: Mapping[str, Any] | None = None


@dataclass(slots=True)
class LoadedPack:
    resolved: ResolvedPack
    manifest: PackManifest


class PackLoader(Protocol):
    kind: str

    def load(self, resolved: ResolvedPack, *, context: PackLoadContext | None = None) -> LoadedPack:
        ...


@dataclass(slots=True)
class LoadedAppPack(LoadedPack):
    runtimeEntry: str | None = None
    runtimeInit: dict[str, Any] = field(default_factory=dict)
    dependencies: dict[str, list[str]] = field(default_factory=dict)

    def createRuntime(
        self,
        *,
        runtimeId: str | None,
        configService: ConfigService,
        configRegistry: SchemaRegistry,
        globalConfigView: ConfigStore,
        kernelMemoryLayers: list[MemoryLayer] | None = None,
        saveRoot: Path | None = None,
    ) -> AppRuntime:
        factory = _resolveRuntimeFactory(self.runtimeEntry)
        params = {
            **self.runtimeInit,
            "runtimeId": runtimeId,
            "configService": configService,
            "configRegistry": configRegistry,
            "globalConfigView": globalConfigView,
            "kernelMemoryLayers": kernelMemoryLayers,
            "saveRoot": saveRoot,
        }
        return factory(**params)


@dataclass(slots=True)
class LoadedViewPack(LoadedPack):
    frontendEntry: str | None = None
    assetsDir: Path | None = None
    contentPacks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LoadedContentPack(LoadedPack):
    filesRoot: Path


@dataclass(slots=True)
class LoadedModPack(LoadedPack):
    modules: list[str] = field(default_factory=list)


class BasePackLoader:
    kind: str

    def load(self, resolved: ResolvedPack, *, context: PackLoadContext | None = None) -> LoadedPack:
        raise NotImplementedError


class AppPackLoader(BasePackLoader):
    kind = "appPack"

    def load(self, resolved: ResolvedPack, *, context: PackLoadContext | None = None) -> LoadedAppPack:
        manifest = resolved.manifest
        runtimeMeta = {}
        if isinstance(manifest.meta, Mapping):
            runtimeMeta = manifest.meta.get("runtime") or {}
        entry = None
        initKw = {}
        if isinstance(runtimeMeta, Mapping):
            entry = runtimeMeta.get("entrypoint") if isinstance(runtimeMeta.get("entrypoint"), str) else None
            initKwRaw = runtimeMeta.get("init")
            if isinstance(initKwRaw, Mapping):
                initKw = {k: v for k, v in initKwRaw.items()}
        dependencies = {
            "viewPacks": _stringList(manifest.meta.get("viewPacks")) if isinstance(manifest.meta, Mapping) else [],
            "contentPacks": _stringList(manifest.meta.get("contentPacks")) if isinstance(manifest.meta, Mapping) else [],
            "mods": _stringList(manifest.meta.get("mods")) if isinstance(manifest.meta, Mapping) else [],
        }
        return LoadedAppPack(
            resolved=resolved,
            manifest=manifest,
            runtimeEntry=entry,
            runtimeInit=initKw,
            dependencies=dependencies,
        )


class ViewPackLoader(BasePackLoader):
    kind = "viewPack"

    def load(self, resolved: ResolvedPack, *, context: PackLoadContext | None = None) -> LoadedViewPack:
        manifest = resolved.manifest
        frontendEntry = None
        assetsDir = None
        contentPacks: list[str] = []
        if isinstance(manifest.meta, Mapping):
            frontendEntry = manifest.meta.get("frontendEntry") if isinstance(manifest.meta.get("frontendEntry"), str) else None
            assets = manifest.meta.get("assetsDir")
            if isinstance(assets, str):
                candidate = resolved.rootDir / assets
                if candidate.exists():
                    assetsDir = candidate
            contentPacks = _stringList(manifest.meta.get("contentPacks"))
        return LoadedViewPack(
            resolved=resolved,
            manifest=manifest,
            frontendEntry=frontendEntry,
            assetsDir=assetsDir,
            contentPacks=contentPacks,
        )


class ContentPackLoader(BasePackLoader):
    kind = "contentPack"

    def load(self, resolved: ResolvedPack, *, context: PackLoadContext | None = None) -> LoadedContentPack:
        return LoadedContentPack(resolved=resolved, manifest=resolved.manifest, filesRoot=resolved.rootDir)


class ModPackLoader(BasePackLoader):
    kind = "mod"

    def load(self, resolved: ResolvedPack, *, context: PackLoadContext | None = None) -> LoadedModPack:
        manifest = resolved.manifest
        modules: list[str] = []
        if isinstance(manifest.meta, Mapping):
            modules = _stringList(manifest.meta.get("modules"))
        return LoadedModPack(resolved=resolved, manifest=manifest, modules=modules)


def _stringList(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    out: list[str] = []
    for entry in value:
        if isinstance(entry, str):
            stripped = entry.strip()
            if stripped:
                out.append(stripped)
    return out


def _resolveRuntimeFactory(entry: str | None) -> Callable[..., AppRuntime]:
    if not entry:
        return _defaultRuntimeFactory
    moduleName, _, callableName = entry.partition(":")
    if not moduleName or not callableName:
        raise ValueError(f"Invalid runtime entrypoint '{entry}'. Expected 'module:callable'.")
    module = import_module(moduleName)
    factory = getattr(module, callableName, None)
    if factory is None or not callable(factory):
        raise AttributeError(f"Entrypoint '{entry}' does not resolve to a callable")
    return factory


def _defaultRuntimeFactory(**kwargs: Any) -> AppRuntime:
    return AppRuntime(
        runtimeId=kwargs.get("runtimeId"),
        configService=kwargs["configService"],
        kernelMemoryLayers=kwargs.get("kernelMemoryLayers"),
        configRegistry=kwargs["configRegistry"],
        globalConfigView=kwargs["globalConfigView"],
        saveRoot=kwargs.get("saveRoot"),
    )
