# file: backend/packs/runtime.py ; version: 7
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
        if len(set(packIds)) != len(packIds):
            raise ValueError("Manual activation plan must not contain the same Pack identity more than once.")
        return cls(packIds=packIds)


class PackResolver:
    """Uniqueness-only resolver over one immutable discovery snapshot.

    Content roots are expected to be stable for the lifetime of a resolver.
    The first lookup discovers and validates every Pack manifest once. Later
    lookups reuse that snapshot, avoiding repeated recursive filesystem scans
    while preserving the resolver's 0/1/>1 candidate semantics.
    """

    def __init__(self, *, roots: tuple[Path, ...]) -> None:
        self._roots = tuple(root.resolve() for root in roots)
        self._candidatesByPackId: dict[str, tuple[PackDefinition, ...]] | None = None

    @staticmethod
    def _definition(manifestPath: Path, manifest: object) -> PackDefinition:
        if not isinstance(manifest, dict):
            raise ValueError(f"Pack manifest must contain a JSON object: {manifestPath}.")
        packId = manifest.get("packId")
        if type(packId) is not str or not packId:
            raise ValueError(f"Pack manifest requires a non-empty string packId: {manifestPath}.")
        entriesSource = manifest.get("codeEntries", [])
        if not isinstance(entriesSource, list):
            raise ValueError(f"Pack {packId!r} has invalid codeEntries.")

        entries: list[CodeEntryDefinition] = []
        seenEntryIds: set[str] = set()
        for item in entriesSource:
            if not isinstance(item, dict) or type(item.get("id")) is not str or type(item.get("source")) is not str:
                raise ValueError(f"Pack {packId!r} has an invalid CodeEntry declaration.")
            entryId = item["id"]
            source = item["source"]
            if not entryId or not source:
                raise ValueError(f"Pack {packId!r} CodeEntry id/source must not be blank.")
            if entryId in seenEntryIds:
                raise ValueError(f"Pack {packId!r} declares duplicate CodeEntry id {entryId!r}.")
            seenEntryIds.add(entryId)
            entries.append(CodeEntryDefinition(codeEntryId=entryId, source=source))

        return PackDefinition(
            packId=packId,
            root=manifestPath.parent.resolve(),
            codeEntries=tuple(entries),
        )

    def _discover(self) -> dict[str, tuple[PackDefinition, ...]]:
        cached = self._candidatesByPackId
        if cached is not None:
            return cached

        discovered: dict[str, list[PackDefinition]] = {}
        for root in self._roots:
            if not root.is_dir():
                continue
            for manifestPath in sorted(root.rglob("manifest.json")):
                try:
                    manifest = json.loads(manifestPath.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as err:
                    raise ValueError(f"Could not read Pack manifest: {manifestPath}.") from err
                definition = self._definition(manifestPath, manifest)
                discovered.setdefault(definition.packId, []).append(definition)

        snapshot = {packId: tuple(candidates) for packId, candidates in discovered.items()}
        self._candidatesByPackId = snapshot
        return snapshot

    def requireSingle(self, packId: str) -> PackDefinition:
        if type(packId) is not str or not packId:
            raise ValueError("Pack identity must be a non-empty exact string.")
        matches = self._discover().get(packId, ())
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


@dataclass(slots=True)
class _LoadedPack:
    pack: PackDefinition
    entries: tuple[_LoadedCodeEntry, ...]
    registrationScope: RegistrationScope


class PackLoader:
    """Mechanical loader/activator for an already ordered manual Pack plan.

    The loader performs no dependency solving or reordering. Its intelligence
    is limited to enforcing lifecycle integrity for the exact Pack sequence it
    was given.
    """

    def __init__(self, *, host: RuntimeHost, resolver: PackResolver) -> None:
        self._host = host
        self._resolver = resolver
        self._loadedPacks: list[_LoadedPack] = []

    def activate(self, plan: ManualActivationPlan) -> None:
        self._host.requireActive()
        checkpoint = len(self._loadedPacks)
        self._host.trace("activation-plan-started", attributes={"packIds": list(plan.packIds)})
        try:
            for packId in plan.packIds:
                self.activatePack(self._resolver.requireSingle(packId))
        except Exception as activationError:
            cleanupErrors = self._closeLoadedPacks(self._loadedPacks[checkpoint:])
            del self._loadedPacks[checkpoint:]
            self._host.trace(
                "activation-plan-failed",
                message=str(activationError),
                attributes={"packIds": list(plan.packIds)},
                level="error",
            )
            if cleanupErrors:
                raise ExceptionGroup(
                    "Activation plan failed and cleanup also reported errors.",
                    [activationError, *cleanupErrors],
                ) from None
            raise
        self._host.trace("activation-plan-completed", attributes={"packIds": list(plan.packIds)})

    def activatePack(self, pack: PackDefinition) -> None:
        self._host.requireActive()
        if any(item.pack.packId == pack.packId for item in self._loadedPacks):
            raise RuntimeError(f"Pack is already active in this ApplicationRun: {pack.packId}.")

        self._host.trace("pack-activation-started", attributes={"packId": pack.packId})
        scope = RegistrationScope()
        entries: list[_LoadedCodeEntry] = []
        loadedModules: list[ModuleType] = []
        registeredOwners: list[str] = []

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
                loadedModules.append(module)

                # Once module code has executed, the CodeEntry may already own
                # resources. Make it cleanup-eligible before invoking onLoad so
                # a failing callback still receives a best-effort onUnload with
                # state=None rather than relying on every Pack to self-clean.
                loadedEntry = _LoadedCodeEntry(identity=identity, pack=pack, module=module, state=None)
                entries.append(loadedEntry)
                context = self._host.createContext(
                    identity=identity,
                    packRoot=pack.root,
                    registrationScope=scope,
                    allowRegistration=True,
                )
                try:
                    callback = getattr(module, "onLoad", None)
                    loadedEntry.state = None if callback is None else callback(context)
                finally:
                    context.invalidate()

            for item in entries:
                self._host.registerCodeEntry(item.identity, item.pack.root)
                registeredOwners.append(item.identity.codeEntryInstanceId)
            scope.publish()

        except Exception as activationError:
            scope.withdraw()
            for ownerId in reversed(registeredOwners):
                self._host.unregisterCodeEntry(ownerId)

            cleanupErrors = self._unloadEntries(entries)
            for module in reversed(loadedModules):
                sys.modules.pop(module.__name__, None)

            self._host.trace(
                "pack-activation-failed",
                message=str(activationError),
                attributes={"packId": pack.packId},
                level="error",
            )
            if cleanupErrors:
                raise ExceptionGroup(
                    f"Pack {pack.packId!r} activation failed and cleanup also reported errors.",
                    [activationError, *cleanupErrors],
                ) from None
            raise

        self._loadedPacks.append(_LoadedPack(pack=pack, entries=tuple(entries), registrationScope=scope))
        self._host.trace(
            "pack-activation-completed",
            attributes={
                "packId": pack.packId,
                "codeEntryInstanceIds": [item.identity.codeEntryInstanceId for item in entries],
            },
        )

    def close(self) -> None:
        cleanupErrors = self._closeLoadedPacks(tuple(self._loadedPacks))
        self._loadedPacks.clear()
        if cleanupErrors:
            raise ExceptionGroup("PackLoader cleanup reported errors.", cleanupErrors)

    def _closeLoadedPacks(self, packs: tuple[_LoadedPack, ...] | list[_LoadedPack]) -> list[Exception]:
        errors: list[Exception] = []
        for loadedPack in reversed(tuple(packs)):
            self._host.trace("pack-unload-started", attributes={"packId": loadedPack.pack.packId})
            loadedPack.registrationScope.withdraw()
            packErrors = self._unloadEntries(list(loadedPack.entries))
            errors.extend(packErrors)
            for item in reversed(loadedPack.entries):
                self._host.unregisterCodeEntry(item.identity.codeEntryInstanceId)
                sys.modules.pop(item.module.__name__, None)
            self._host.trace(
                "pack-unload-completed" if not packErrors else "pack-unload-failed",
                attributes={"packId": loadedPack.pack.packId, "errorCount": len(packErrors)},
                level="info" if not packErrors else "error",
            )
        return errors

    def _unloadEntries(self, entries: list[_LoadedCodeEntry]) -> list[Exception]:
        errors: list[Exception] = []
        for item in reversed(entries):
            callback = getattr(item.module, "onUnload", None)
            if callback is None:
                continue
            cleanupScope = RegistrationScope()
            context = self._host.createContext(
                identity=item.identity,
                packRoot=item.pack.root,
                registrationScope=cleanupScope,
                allowRegistration=False,
            )
            try:
                callback(context, item.state)
            except Exception as err:
                errors.append(err)
            finally:
                context.invalidate()
                cleanupScope.withdraw()
        return errors

    @staticmethod
    def _loadModule(*, pack: PackDefinition, definition: CodeEntryDefinition, instanceId: str) -> ModuleType:
        packRoot = pack.root.resolve()
        sourcePath = (packRoot / definition.source).resolve()
        if not sourcePath.is_relative_to(packRoot):
            raise ValueError(
                f"CodeEntry source escapes Pack root for {pack.packId!r}/{definition.codeEntryId!r}: {definition.source!r}.",
            )
        if not sourcePath.is_file():
            raise FileNotFoundError(f"CodeEntry source does not exist: {sourcePath}.")
        if sourcePath.suffix != ".py":
            raise ValueError(f"Python CodeEntry source must use a .py file: {sourcePath}.")

        moduleName = (
            f"_actant_{pack.packId.replace('.', '_')}_"
            f"{definition.codeEntryId.replace('.', '_')}_{instanceId.replace('-', '_')}"
        )
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
