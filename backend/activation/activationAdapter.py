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

    call invokes the named callable with ctx.

    When request is None, the callable is invoked with only ctx:

        callable(ctx)

    When request is supplied, including an empty object, the callable is
    invoked with ctx and request:

        callable(ctx, request)

    Therefore, request=None controls the callable signature. It does not mean
    that None is passed as the request argument.

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
