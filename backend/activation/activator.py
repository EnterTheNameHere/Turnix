# file: backend/activation/activator.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.activation.activationErrors import ActivationError, ActivationFailureContext
from backend.activation.activationReport import ActivatedEntry, ActivationReport
from backend.context.modCallContext import createModCallContext
from backend.core.errors import UsageError
from backend.core.validation import typeName

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.activation.activationAdapter import ActivationAdapter
    from backend.activation.activationAdapterKind import ActivationAdapterKind
    from backend.activation.activationEntry import PythonActivationEntry
    from backend.activation.activationPlan import ActivationPlan
    from backend.runtime.runtimeServices import RuntimeServices
    from backend.tracing.devTrace import DevTraceSink


@dataclass(frozen=True)
class LoadedMod:
    entryId: str
    ownerId: str
    adapterKind: ActivationAdapterKind
    instance: object


def activatePlan(
    *,
    plan: ActivationPlan,
    runtime: RuntimeServices,
    adapters: Mapping[ActivationAdapterKind, ActivationAdapter],
    sink: DevTraceSink,
) -> tuple[ActivationReport, tuple[LoadedMod, ...]]:
    activatedEntries: list[ActivatedEntry] = []
    loadedMods: list[LoadedMod] = []

    sink.emit(
        reason="ActivationPlanStarted",
        message="activation plan started",
        attrs={
            "planId": plan.planId,
            "entryCount": len(plan.entries),
        },
    )

    for entry in plan.entries:
        sink.emit(
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

        adapter = getAdapterForEntry(
            entry=entry,
            adapters=adapters,
        )
        ctx = createModCallContext(
            applicationId=runtime.applicationId,
            applicationRunId=runtime.applicationRunId,
            actingPackId=entry.ownerId,
            capabilities=runtime.capabilities,
            hooks=runtime.hooks,
            memoryTransaction=None,
            configTransaction=None,
            io=runtime.io,
            trace=sink,
            stageId="activation",
        )

        try:
            mod = adapter.load(entry=entry)
            adapter.call(
                mod=mod,
                callableName=entry.callableName,
                ctx=ctx,
            )
        except ActivationError:
            raise

        except Exception as err:
            sink.emit(
                reason="ActivationPlanEntryFailed",
                message="activation plan entry failed",
                attrs={
                    "planId": plan.planId,
                    "entryId": entry.entryId,
                    "ownerId": entry.ownerId,
                    "adapterKind": entry.adapterKind,
                    "sourcePath": str(entry.sourcePath),
                    "callableName": entry.callableName,
                    "causeType": typeName(err),
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

        finally:
            ctx.invalidate()

        activatedEntries.append(
            ActivatedEntry(
                entryId=entry.entryId,
                ownerId=entry.ownerId,
                adapterKind=entry.adapterKind,
                sourcePath=entry.sourcePath,
                callableName=entry.callableName,
            ),
        )
        loadedMods.append(
            LoadedMod(
                entryId=entry.entryId,
                ownerId=entry.ownerId,
                adapterKind=entry.adapterKind,
                instance=mod,
            ),
        )

        sink.emit(
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

    report = ActivationReport(
        planId=plan.planId,
        activatedEntries=tuple(activatedEntries),
    )

    sink.emit(
        reason="ActivationPlanCompleted",
        message="activation plan completed",
        attrs={
            "planId": plan.planId,
            "activatedEntryIds": report.activatedEntryIds,
        },
    )

    return report, tuple(loadedMods)


def getAdapterForEntry(
    *,
    entry: PythonActivationEntry,
    adapters: Mapping[ActivationAdapterKind, ActivationAdapter],
) -> ActivationAdapter:
    try:
        return adapters[entry.adapterKind]
    except KeyError as err:
        raise UsageError(
            f"No activation adapter registered for adapter kind "
            f"{entry.adapterKind}.",
        ) from err


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
