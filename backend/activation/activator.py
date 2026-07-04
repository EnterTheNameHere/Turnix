# file: backend/activation/activator.py
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.activation.activationErrors import ActivationError, ActivationFailureContext
from backend.activation.activationReport import ActivatedEntry, ActivationReport
from backend.context.modCallContext import ModCallContext
from backend.core.errors import UsageError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.activation.activationAdapter import ActivationAdapter
    from backend.activation.activationAdapterKind import ActivationAdapterKind
    from backend.activation.activationEntry import PythonActivationEntry
    from backend.activation.activationPlan import ActivationPlan
    from backend.capabilities.registry import CapabilityRegistry
    from backend.tracing.devTrace import DevTraceSink


def activatePlan(
    *,
    plan: ActivationPlan,
    registry: CapabilityRegistry,
    adapters: Mapping[ActivationAdapterKind, ActivationAdapter],
    sink: DevTraceSink | None = None,
) -> ActivationReport:
    """
    Activate entries in plan order.

    This function owns activation plan iteration, per-entry adapter selection,
    and per-entry context creation. It does not create the plan, discover Packs,
    resolve dependencies, verify capabilities, persist activation state, recover,
    or rollback.
    """
    activatedEntries: list[ActivatedEntry] = []

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
                "callableName": entry.callableName,
            },
        )

        try:
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

        except ActivationError:
            raise

        except Exception as err:
            emit(
                sink=sink,
                reason="ActivationPlanEntryFailed",
                message="activation plan entry failed",
                attrs={
                    "planId": plan.planId,
                    "entryId": entry.entryId,
                    "ownerId": entry.ownerId,
                    "adapterKind": entry.adapterKind,
                    "sourcePath": str(entry.sourcePath),
                    "callableName": entry.callableName,
                    "causeType": type(err).__name__,
                    "cause": str(err),
                },
            )

            raise ActivationError(
                context=createFailureContext(
                    plan=plan,
                    entry=entry,
                ),
                cause=err,
            ) from err

        activatedEntries.append(
            ActivatedEntry(
                entryId=entry.entryId,
                ownerId=entry.ownerId,
                adapterKind=entry.adapterKind,
                sourcePath=entry.sourcePath,
                callableName=entry.callableName,
            )
        )

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
                "callableName": entry.callableName,
            },
        )

    return ActivationReport(
        planId=plan.planId,
        activatedEntries=tuple(activatedEntries),
    )


def getAdapterForEntry(
    *,
    entry: PythonActivationEntry,
    adapters: Mapping[ActivationAdapterKind, ActivationAdapter],
) -> ActivationAdapter:
    try:
        return adapters[entry.adapterKind]
    except KeyError as err:
        raise UsageError(f"No activation adapter registered for adapter kind {entry.adapterKind}.") from err


def createFailureContext(
    *,
    plan: ActivationPlan,
    entry: PythonActivationEntry,
) -> ActivationFailureContext:
    return ActivationFailureContext(
        planId=plan.planId,
        entryId=entry.entryId,
        ownerId=entry.ownerId,
        adapterKind=entry.adapterKind,
        sourcePath=entry.sourcePath,
        callableName=entry.callableName,
    )


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
