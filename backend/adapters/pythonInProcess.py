# file: backend/adapters/pythonInProcess.py
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from backend.activation.activationEntry import PythonActivationEntry
from backend.context.modCallContext import ModCallContext
from backend.core.errors import UsageError
from backend.tracing.devTrace import DevTraceSink


type ActivationCallable = Callable[[ModCallContext], object]


class PythonInProcessAdapter:
    """
    Loads declared Python activation entries by source file path.

    This adapter executes trusted Python code in the current process.

    It is not sandboxed. It does not isolate imports, memory, global state,
    process exit, environment access, or filesystem access. It is appropriate
    only for trusted local/bootstrap execution.
    """

    def __init__(
        self,
        *,
        sink: DevTraceSink | None = None,
    ) -> None:
        self._sink = sink

    def loadAndCall(
        self,
        *,
        entry: PythonActivationEntry,
        ctx: ModCallContext,
    ) -> object:
        sourcePath = self._resolveSourcePath(entry.sourcePath)

        self._emit(
            reason="PythonActivationStarted",
            message="python activation started",
            attrs={
                "entryId": entry.entryId,
                "ownerId": entry.ownerId,
                "sourcePath": str(sourcePath),
                "callableName": entry.callableName,
            },
        )

        module = self._loadModule(entry=entry, sourcePath=sourcePath)
        activationCallable = self._getActivationCallable(
            module=module,
            callableName=entry.callableName,
            entryId=entry.entryId,
        )

        result = activationCallable(ctx)

        self._emit(
            reason="PythonActivationCompleted",
            message="python activation completed",
            attrs={
                "entryId": entry.entryId,
                "ownerId": entry.ownerId,
                "sourcePath": str(sourcePath),
                "callableName": entry.callableName,
            },
        )

        return result

    def _resolveSourcePath(self, sourcePath: Path) -> Path:
        if not isinstance(sourcePath, Path):
            raise UsageError(f"sourcePath must be a pathlib.Path, got {type(sourcePath)}.")

        resolvedPath = sourcePath.resolve()

        if not resolvedPath.exists():
            raise UsageError(f"Python activation source path does not exists: {resolvedPath}.")

        if not resolvedPath.is_file():
            raise UsageError(f"Python activation source path is not a file: {resolvedPath}.")

        if resolvedPath.suffix != ".py":
            raise UsageError(f"Python activation source path must have a .py extension: {resolvedPath}.")

        return resolvedPath

    def _loadModule(
        self,
        *,
        entry: PythonActivationEntry,
        sourcePath: Path,
    ) -> ModuleType:
        moduleName = createInternalModuleName(entry.entryId)

        spec = importlib.util.spec_from_file_location(moduleName, sourcePath)
        if spec is None:
            raise UsageError(f"Could not create module spec for {sourcePath}")

        loader = spec.loader
        if loader is None:
            raise UsageError(f"Module spec has no loader for {sourcePath}")

        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)

        return module

    def _getActivationCallable(
        self,
        *,
        module: ModuleType,
        callableName: str,
        entryId: str,
    ) -> ActivationCallable:
        if not hasattr(module, callableName):
            raise UsageError(f"Python activation entry {entryId} does not define callable {callableName}.")

        candidate = getattr(module, callableName)
        if not callable(candidate):
            raise UsageError(f"Python activation entry {entryId} attribute {callableName} is not callable.")

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


def createInternalModuleName(entryId: str) -> str:
    safeChars: list[str] = []

    for char in entryId:
        if char.isalnum():
            safeChars.append(char)
            continue

        safeChars.append("_")

    safeName = "".join(safeChars).strip("_")
    if not safeName:
        safeName = "entry"

    return f"_actant_activation_{safeName}"
