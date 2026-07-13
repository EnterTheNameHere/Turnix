# file: backend/activation/activationAdapter.py
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.activation.activationEntry import PythonActivationEntry
    from backend.context.modCallContext import ModCallContext
    from backend.io.jsonio import JsonObject


class ActivationAdapter(Protocol):
    """
    Loads mod entries and invokes callables on loaded mod instances.

    load executes the adapter-specific entry and returns its loaded mod
    instance. The returned instance may be reused for multiple calls.

    call invokes the named callable with ctx. When request is supplied, it is
    passed as the second argument.

    The adapter does not determine dependency order, activation phases,
    duplicate-loading policy, reuse, unloading, or reloading.
    """

    def load(
        self,
        *,
        entry: PythonActivationEntry,
    ) -> object:
        ...

    def call(
        self,
        *,
        mod: object,
        callableName: str,
        ctx: ModCallContext,
        request: JsonObject | None = None,
    ) -> object:
        ...
