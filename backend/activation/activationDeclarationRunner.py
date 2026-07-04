# file: backend/activation/activationDeclarationRunner.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.activation.activationDeclaration import materializeActivationPlan
from backend.activation.activationDeclarationLoader import loadActivationDeclarationFromSource
from backend.activation.activationDeclarationSource import ActivationDeclarationSource
from backend.activation.activator import activatePlan
from backend.content.contentRootCatalog import ContentRootCatalog, getContentRoot
from backend.core.errors import UsageError
from backend.core.validation import typeName

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.activation.activationAdapter import ActivationAdapter
    from backend.activation.activationAdapterKind import ActivationAdapterKind
    from backend.activation.activationReport import ActivationReport
    from backend.capabilities.registry import CapabilityRegistry
    from backend.tracing.devTrace import DevTraceSink


@dataclass(frozen=True)
class ActivationDeclarationRunReport:
    """
    Source-aware activation declaration run result.

    This is not discovery, dependency solving, Pack loading, or lifecycle state.
    """

    source: ActivationDeclarationSource
    activationReport: ActivationReport


@dataclass(frozen=True)
class ActivationDeclarationBatchReport:
    """
    Report for running an explicit tuple of activation declaration sources.

    The source tuple is supplied by platform code. This object does not
    discover sources.
    """

    runs: tuple[ActivationDeclarationRunReport, ...]


def runActivationDeclarationSource(
    *,
    catalog: ContentRootCatalog,
    source: ActivationDeclarationSource,
    registry: CapabilityRegistry,
    adapters: Mapping[ActivationAdapterKind, ActivationAdapter],
    sink: DevTraceSink,
) -> ActivationDeclarationRunReport:
    if not isinstance(source, ActivationDeclarationSource):
        raise UsageError(f"source must be an ActivationDeclarationSource, not {typeName(source)}.")

    contentRoot = getContentRoot(
        catalog=catalog,
        rootId=source.rootId,
    )

    declaration = loadActivationDeclarationFromSource(
        catalog=catalog,
        source=source,
    )

    plan = materializeActivationPlan(
        declaration=declaration,
        basePath=contentRoot.rootPath,
    )

    sink.emit(
        reason="ActivationDeclarationSourceRunStarted",
        message="activation declaration source command started",
        attrs={
            "rootId": contentRoot.rootId,
            "rootPath": str(contentRoot.rootPath),
            "declarationPath": source.declarationPath.as_posix(),
            "planId": plan.planId,
            "entryCount": len(plan.entries),
        },
    )

    activationReport = activatePlan(
        plan=plan,
        registry=registry,
        adapters=adapters,
        sink=sink,
    )

    expectedEntryIds = tuple(entry.entryId for entry in plan.entries)
    if activationReport.activatedEntryIds != expectedEntryIds:
        raise UsageError(
            f"Unexpected activated entry IDs: expected {expectedEntryIds!r}, "
            f"got {activationReport.activatedEntryIds!r}.",
        )

    sink.emit(
        reason="ActivationDeclarationSourceRunCompleted",
        message="activation declaration source command completed",
        attrs={
            "rootId": source.rootId,
            "declarationPath": source.declarationPath.as_posix(),
            "planId": activationReport.planId,
            "entryCount": len(activationReport.activatedEntries),
        },
    )

    return ActivationDeclarationRunReport(
        source=source,
        activationReport=activationReport,
    )


def runActivationDeclarationSources(
    *,
    catalog: ContentRootCatalog,
    sources: tuple[ActivationDeclarationSource, ...],
    registry: CapabilityRegistry,
    adapters: Mapping[ActivationAdapterKind, ActivationAdapter],
    sink: DevTraceSink,
) -> ActivationDeclarationBatchReport:
    cleanSources = validateActivationDeclarationSources(
        sources=sources,
    )

    sink.emit(
        reason="ActivationDeclarationBatchRunStarted",
        message="activation declaration batch run started",
        attrs={
            "sourceCount": len(cleanSources),
        },
    )

    runs: list[ActivationDeclarationRunReport] = [
        runActivationDeclarationSource(
            catalog=catalog,
            source=source,
            registry=registry,
            adapters=adapters,
            sink=sink,
        )
        for source in cleanSources
    ]

    report = ActivationDeclarationBatchReport(
        runs=tuple(runs),
    )

    sink.emit(
        reason="ActivationDeclarationBatchRunCompleted",
        message="activation declaration batch run completed",
        attrs={
            "sourceCount": len(cleanSources),
            "planIds": tuple(run.activationReport.planId for run in report.runs),
        },
    )

    return report


def validateActivationDeclarationSources(
    *,
    sources: tuple[ActivationDeclarationSource, ...],
) -> tuple[ActivationDeclarationSource, ...]:
    if not isinstance(sources, tuple):
        raise UsageError(f"sources must be a tuple, not {typeName(sources)}.")

    if not sources:
        raise UsageError("sources must not be empty.")

    seenSourceKeys: set[tuple[str, str]] = set()

    for index, source in enumerate(sources):
        if not isinstance(source, ActivationDeclarationSource):
            raise UsageError(f"sources[{index}] must be an ActivationDeclarationSource, not {typeName(source)}.")

        sourceKey = (
            source.rootId,
            str(source.declarationPath),
        )
        if sourceKey in seenSourceKeys:
            raise UsageError(
                f"Duplicate activation declaration source: {sourceKey}, "
                f"declarationPath={source.declarationPath.as_posix()}.",
            )

        seenSourceKeys.add(sourceKey)

    return sources

