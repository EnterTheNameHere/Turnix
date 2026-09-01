# file: backend/packs/runtime.py ; version: 3
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from backend.context.codeEntryContext import CodeEntryIdentity
from backend.core.runtimeIds import newRuntimeId
from backend.registration import RegistrationScope

if TYPE_CHECKING:
    from backend.runtime.runtimeHost import RuntimeHost

__all__ = ["ManualActivationPlan", "PackDefinition", "PackLoader", "PackResolver"]


@dataclass(frozen=True, slots=True)
class CodeEntryDefinition:
    codeEntryId: str
    source: str


@dataclass(frozen=True, slots=True)
class PackDefinition:
    packId: str
    root: Path
    codeEntries: tuple[CodeEntryDefinition, ...]


@dataclass(frozen=True, slots=True)
class ManualActivationPlan:
    """Explicit ordered Pack list. No dependency semantics are inferred."""

    packIds: tuple[str, ...]

    @classmethod
    def fromJson(cls, value: object) -> "ManualActivationPlan":
        if not isinstance(value, dict) or not isinstance(value.get("packs"), list):
            raise ValueError("Activation plan must contain a 'packs' list.")
        packIds = tuple(value["packs"])
        if not all(type(item) is str and item for item in packIds):
            raise ValueError("Every activation plan Pack identity must be a non-empty string.")
        return cls(packIds=packIds)


class PackResolver:
    """Uniqueness-only resolver skeleton: 0 or >1 candidates are errors."""

    def __init__(self, *, roots: tuple[Path, ...]) -> None:
        self._roots = tuple(root.resolve() for root in roots)

    def requireSingle(self, packId: str) -> PackDefinition:
        matches: list[PackDefinition] = []
        for root in self._roots:
            for manifestPath in sorted(root.rglob("manifest.json")):
                manifest = json.loads(manifestPath.read_text(encoding="utf-8"))
                if manifest.get("packId") != packId:
                    continue
                entriesSource = manifest.get("codeEntries", [])
                if not isinstance(entriesSource, list):
                    raise ValueError(f"Pack {packId!r} has invalid codeEntries.")
                entries: list[CodeEntryDefinition] = []
                for item in entriesSource:
                    if not isinstance(item, dict) or type(item.get("id")) is not str or type(item.get("source")) is not str:
                        raise ValueError(f"Pack {packId!r} has an invalid CodeEntry declaration.")
                    entries.append(CodeEntryDefinition(codeEntryId=item["id"], source=item["source"]))
                matches.append(PackDefinition(packId=packId, root=manifestPath.parent, codeEntries=tuple(entries)))
        if not matches:
            raise LookupError(f"Pack was not found: {packId}.")
        if len(matches) != 1:
            roots = ", ".join(str(item.root) for item in matches)
            raise RuntimeError(f"Pack resolution is ambiguous for {packId!r}: {roots}.")
        return matches[0]


@dataclass(slots=True)
class _LoadedCodeEntry:
    identity: CodeEntryIdentity
    pack: PackDefinition
    module: ModuleType
    state: object | None


class PackLoader:
    """Mechanical loader/activator for an already ordered manual Pack plan."""

    def __init__(self, *, host: RuntimeHost, resolver: PackResolver) -> None:
        self._host = host
        self._resolver = resolver
        self._loaded: list[_LoadedCodeEntry] = []

    def activate(self, plan: ManualActivationPlan) -> None:
        for packId in plan.packIds:
            self.activatePack(self._resolver.requireSingle(packId))

    def activatePack(self, pack: PackDefinition) -> None:
        scope = RegistrationScope()
        activated: list[_LoadedCodeEntry] = []
        try:
            for definition in pack.codeEntries:
                instanceId = newRuntimeId()
                identity = CodeEntryIdentity(
                    applicationId=self._host.applicationRun.application.applicationId,
                    applicationRunId=self._host.applicationRun.applicationRunId,
                    packId=pack.packId,
                    codeEntryId=definition.codeEntryId,
                    codeEntryInstanceId=instanceId,
                )
                module = self._loadModule(pack=pack, definition=definition, instanceId=instanceId)
                context = self._host.createContext(identity=identity, packRoot=pack.root, registrationScope=scope)
                try:
                    callback = getattr(module, "onLoad", None)
                    state = None if callback is None else callback(context)
                finally:
                    context.invalidate()
                activated.append(_LoadedCodeEntry(identity=identity, pack=pack, module=module, state=state))
            scope.publish()
        except Exception:
            scope.withdraw()
            for item in reversed(activated):
                callback = getattr(item.module, "onUnload", None)
                if callback is not None:
                    try:
                        cleanupScope = RegistrationScope()
                        context = self._host.createContext(identity=item.identity, packRoot=item.pack.root,
                                                           registrationScope=cleanupScope)
                        try:
                            callback(context, item.state)
                        finally:
                            context.invalidate()
                            cleanupScope.withdraw()
                    except Exception:
                        pass
            raise
        self._loaded.extend(activated)
        for item in activated:
            self._host.registerCodeEntry(item.identity, item.pack.root)

    def close(self) -> None:
        for item in reversed(self._loaded):
            callback = getattr(item.module, "onUnload", None)
            if callback is not None:
                scope = RegistrationScope()
                context = self._host.createContext(identity=item.identity, packRoot=item.pack.root, registrationScope=scope)
                try:
                    callback(context, item.state)
                finally:
                    context.invalidate()
                    scope.withdraw()
            self._host.capabilities.unregisterOwnedBy(item.identity.codeEntryInstanceId)
            self._host.llmProviders.unregisterOwnedBy(item.identity.codeEntryInstanceId)
            sys.modules.pop(item.module.__name__, None)
        self._loaded.clear()

    @staticmethod
    def _loadModule(*, pack: PackDefinition, definition: CodeEntryDefinition, instanceId: str) -> ModuleType:
        sourcePath = (pack.root / definition.source).resolve()
        if not sourcePath.is_file():
            raise FileNotFoundError(f"CodeEntry source does not exist: {sourcePath}.")
        moduleName = f"_actant_{pack.packId.replace('.', '_')}_{definition.codeEntryId.replace('.', '_')}_{instanceId.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(moduleName, sourcePath)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not create Python module spec for {sourcePath}.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[moduleName] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(moduleName, None)
            raise
        return module
