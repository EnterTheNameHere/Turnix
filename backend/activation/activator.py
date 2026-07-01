# file: backend/activation/activator.py
from __future__ import annotations

from collections.abc import Mapping

from backend.activation.activationAdapterKind import ActivationAdapterKind
from backend.activation.activationEntry import PythonActivationEntry
from backend.activation.activationPlan import ActivationPlan
from backend.adapters.pythonInProcess import PythonInProcessAdapter
from backend.capabilities.registry import CapabilityRegistry
from backend.context.modCallContext import ModCallContext
from backend.core.errors import UsageError
from backend.tracing.devTrace import DevTraceSink


def activatePlan(
    *,
    plan: ActivationPlan,
    registry: CapabilityRegistry,
    adapters: Mapping[ActivationAdapterKind, PythonInProcessAdapter],
    sink: DevTraceSink | None = None,
) -> tuple[str, ...]:
    """
    Activate entries in plan order.

    This function owns activation plan iteration, per-entry adapter selection,
    and per-entry context creation. It does not create the plan, discover Packs,
    resolve dependencies, verify capabilities, or persist activation state.
    """
    activatedEntryIds: list[str] = []

    for entry in plan.entries:
        emit(
            sink=sink,
            reason="ActivationPlanEntryStarted",
            message="activation plan entry started",
            attrs={
                "planId": plan.planId,
                "entryId": entry.entryId,
                "ownerId": entry.ownerId,
                "adapterKind": entry.adapterKind,
                "sourcePath": str(entry.sourcePath),
            },
        )

        adapter = getAdapterForEntry(
            entry=entry,
            adapters=adapters,
        )

        ctx = ModCallContext(
            ownerId=entry.ownerId,
            capabilityRegistry=registry,
        )

        adapter.loadAndCall(
            entry=entry,
            ctx=ctx,
        )

        activatedEntryIds.append(entry.entryId)

        emit(
            sink=sink,
            reason="ActivationPlanEntryCompleted",
            message="activation plan entry completed",
            attrs={
                "planId": plan.planId,
                "entryId": entry.entryId,
                "ownerId": entry.ownerId,
                "adapterKind": entry.adapterKind,
                "sourcePath": str(entry.sourcePath),
            },
        )

    return tuple(activatedEntryIds)


def getAdapterForEntry(
    *,
    entry: PythonActivationEntry,
    adapters: Mapping[ActivationAdapterKind, PythonInProcessAdapter],
) -> PythonInProcessAdapter:
    try:
        return adapters[entry.adapterKind]
    except KeyError as err:
        raise UsageError(f"No activation adapter registered for adapter kind {entry.adapterKind}.") from err


def emit(
    *,
    sink: DevTraceSink | None,
    reason: str,
    message: str,
    attrs: dict[str, object],
) -> None:
    if sink is None:
        return

    sink.emit(
        reason=reason,
        message=message,
        attrs=attrs,
    )
