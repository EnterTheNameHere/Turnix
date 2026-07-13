# file: backend/adapters/pythonInProcess.py
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast

from backend.activation.activationAdapter import ActivationAdapter
from backend.activation.activationAdapterKind import ActivationAdapterKind
from backend.activation.activationEntry import PythonActivationEntry
from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString, typeName

if TYPE_CHECKING:
    from backend.context.modCallContext import ModCallContext
    from backend.io.jsonio import JsonObject
    from backend.tracing.devTrace import DevTraceSink


type PythonModCallable = Callable[..., object]


@dataclass(frozen=True)
class PythonLoadedMod:
    entryId: str
    ownerId: str
    sourcePath: Path
    moduleName: str
    module: ModuleType
    adapterKind: ActivationAdapterKind


class PythonInProcessAdapter(ActivationAdapter):
    """
    Loads Python mod entry files into the current process.

    Each call to load executes the supplied Python source and returns a new
    loaded mod instance. The adapter does not cache, deduplicate, order,
    unload, or reload loaded mods.

    This adapter executes trusted Python code in the current process.

    It is not sandboxed. It does not isolate imports, memory, global state,
    process exit, environment access, or filesystem access. It is appropriate
    only for trusted local or bootstrap execution.
    """

    def __init__(
        self,
        *,
        sink: DevTraceSink | None = None,
    ) -> None:
        self._sink = sink

    def load(
        self,
        *,
        entry: PythonActivationEntry,
    ) -> PythonLoadedMod:
        if not isinstance(entry, PythonActivationEntry):
            raise UsageError(
                "PythonInProcessAdapter entry must be a "
                f"PythonActivationEntry, not {typeName(entry)}.",
            )

        sourcePath = self._resolveSourcePath(entry.sourcePath)
        moduleName = createInternalModuleName(
            ownerId=entry.ownerId,
            entryId=entry.entryId,
        )

        self._emit(
            reason="PythonModLoadStarted",
            message="python mod load started",
            attrs={
                "entryId": entry.entryId,
                "ownerId": entry.ownerId,
                "sourcePath": str(sourcePath),
                "moduleName": moduleName,
            },
        )

        module = self._loadModule(
            moduleName=moduleName,
            sourcePath=sourcePath,
        )

        mod = PythonLoadedMod(
            entryId=entry.entryId,
            ownerId=entry.ownerId,
            sourcePath=sourcePath,
            moduleName=moduleName,
            module=module,
            adapterKind=ActivationAdapterKind.PYTHON_IN_PROCESS,
        )

        self._emit(
            reason="PythonModLoadCompleted",
            message="python mod load completed",
            attrs={
                "entryId": mod.entryId,
                "ownerId": mod.ownerId,
                "sourcePath": str(mod.sourcePath),
                "moduleName": mod.moduleName,
            },
        )

        return mod

    def call(
        self,
        *,
        mod: object,
        callableName: str,
        ctx: ModCallContext,
        request: JsonObject | None = None,
    ) -> object:
        """
        Invoke a callable on a loaded Python mod.

        If request is None, invokes callable(ctx). Otherwise, invokes
        callable(ctx, request). Supplying an empty object therefore still
        selects the two-argument callable form.
        """
        if not isinstance(mod, PythonLoadedMod):
            raise UsageError(
                f"PythonInProcessAdapter mod must be a "
                f"PythonLoadedMod, not {typeName(mod)}.",
            )

        cleanCallableName = requireExactNonBlankString(
            callableName,
            "callableName",
        )
        modCallable = self._getModCallable(
            module=mod.module,
            callableName=cleanCallableName,
            entryId=mod.entryId,
        )

        self._emit(
            reason="PythonModCallStarted",
            message="python mod call started",
            attrs={
                "entryId": mod.entryId,
                "ownerId": mod.ownerId,
                "sourcePath": str(mod.sourcePath),
                "moduleName": mod.moduleName,
                "callableName": cleanCallableName,
                "hasRequest": request is not None,
            },
        )

        result = modCallable(ctx) if request is None else modCallable(ctx, request)

        self._emit(
            reason="PythonModCallCompleted",
            message="python mod call completed",
            attrs={
                "entryId": mod.entryId,
                "ownerId": mod.ownerId,
                "sourcePath": str(mod.sourcePath),
                "moduleName": mod.moduleName,
                "callableName": cleanCallableName,
                "hasRequest": request is not None,
            },
        )

        return result

    def _resolveSourcePath(self, sourcePath: Path) -> Path:
        if not isinstance(sourcePath, Path):
            raise UsageError(
                f"sourcePath must be a pathlib.Path, got {typeName(sourcePath)}.",
            )

        resolvedPath = sourcePath.resolve()

        if not resolvedPath.exists():
            raise UsageError(
                f"Python mod source path does not exist: {resolvedPath}.",
            )

        if not resolvedPath.is_file():
            raise UsageError(
                f"Python mod source path is not a file: {resolvedPath}.",
            )

        if resolvedPath.suffix != ".py":
            raise UsageError(
                f"Python mod source path must have a .py extension: {resolvedPath}.",
            )

        return resolvedPath

    def _loadModule(
        self,
        *,
        moduleName: str,
        sourcePath: Path,
    ) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            moduleName,
            sourcePath,
        )
        if spec is None:
            raise UsageError(
                f"Could not create module spec for {sourcePath}.",
            )

        loader = spec.loader
        if loader is None:
            raise UsageError(
                f"Module spec has no loader for {sourcePath}.",
            )

        module = importlib.util.module_from_spec(spec)

        missing = object()
        previousModule: ModuleType | object = sys.modules.get(moduleName, missing)
        sys.modules[moduleName] = module

        try:
            loader.exec_module(module)
        except Exception:
            if previousModule is missing:
                sys.modules.pop(moduleName, None)
            else:
                sys.modules[moduleName] = cast(ModuleType, previousModule)
            raise

        return module

    def _getModCallable(
        self,
        *,
        module: ModuleType,
        callableName: str,
        entryId: str,
    ) -> PythonModCallable:
        if not hasattr(module, callableName):
            raise UsageError(
                f"Python mod {entryId} does not define "
                f"callable {callableName}.",
            )

        candidate = getattr(module, callableName)
        if not callable(candidate):
            raise UsageError(
                f"Python mod {entryId} attribute "
                f"{callableName} is not callable.",
            )

        return candidate

    def _emit(
        self,
        *,
        reason: str,
        message: str,
        attrs: dict[str, object],
    ) -> None:
        if self._sink is None:
            return

        self._sink.emit(
            reason=reason,
            message=message,
            attrs=attrs,
        )


def createInternalModuleName(
    *,
    ownerId: str,
    entryId: str,
) -> str:
    # TODO: Add collision resistance. Punctuation is normalized to "_", so
    # identifiers such as "owner-a" and "owner.a" produce the same name.
    identity = f"{ownerId}_{entryId}"
    safeChars: list[str] = []

    for char in identity:
        if char.isascii() and char.isalnum():
            safeChars.append(char)
            continue

        safeChars.append("_")

    safeName = "".join(safeChars).strip("_")
    if not safeName:
        safeName = "mod"

    return f"_actant_activation_{safeName}"
