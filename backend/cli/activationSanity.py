# file: backend/cli/activationSanity.py
from __future__ import annotations

from pathlib import PurePosixPath

from backend.activation.activationAdapterKind import ActivationAdapterKind
from backend.activation.activationDeclaration import createActivationDeclaration, materializeActivationPlan
from backend.activation.activationDeclarationRunner import runActivationDeclarationSources
from backend.activation.activationEntry import createPythonActivationEntry
from backend.activation.activationErrors import ActivationError
from backend.activation.activationSpec import createActivationSpec
from backend.activation.activator import activatePlan
from backend.adapters.pythonInProcess import PythonInProcessAdapter
from backend.bootstrap.bootstrapActivationSources import createBootstrapActivationDeclarationSources
from backend.capabilities.registry import CapabilityRegistry
from backend.content.contentRoot import createContentRoot
from backend.content.contentRootCatalog import createContentRootCatalog
from backend.context.modCallContext import ModCallContext
from backend.core.errors import UsageError
from backend.core.paths import getRepoRoot
from backend.core.validation import typeName
from backend.tracing.devTrace import DevTraceSink

SUCCESS = 0


def runActivationSanity() -> int:
    sink = DevTraceSink()

    repoRoot = getRepoRoot()
    ownerId = "first-party.bootstrap"
    entryId = "first-party.bootstrap.activation"
    capabilityId = "bootstrap.activationEcho@1"
    sourcePath = repoRoot / "first-party" / "bootstrap" / "activation.py"

    sink.emit(
        reason="ActivationSanityStarted",
        message="activation sanity command started",
        attrs={
            "ownerId": ownerId,
            "entryId": entryId,
            "capabilityId": capabilityId,
            "sourcePath": str(sourcePath),
        },
    )

    registry = CapabilityRegistry()
    ctx = ModCallContext(
        ownerId=ownerId,
        capabilityRegistry=registry,
    )

    entry = createPythonActivationEntry(
        entryId=entryId,
        sourcePath=sourcePath,
        ownerId=ownerId,
        callableName="onLoad",
    )

    adapter = PythonInProcessAdapter(sink=sink)
    adapter.loadAndCall(
        entry=entry,
        ctx=ctx,
    )

    if not registry.has(capabilityId):
        raise UsageError(f"Activation did not register expected capability: {capabilityId}.")

    result = registry.call(capabilityId, {"text": "hello"})

    if result != {"text": "hello"}:
        raise UsageError(f"Unexpected capability result: expected {{'text': 'hello'}}, got {result!r}.")

    sink.emit(
        reason="ActivationSanityCompleted",
        message="activation sanity command completed",
        attrs={
            "ownerId": ownerId,
            "entryId": entryId,
            "capabilityId": capabilityId,
            "result": result,
        },
    )

    print("Actant activation sanity OK")
    print(f"entry: {entryId}")
    print(f"sourcePath: {sourcePath}")
    print(f"registered: {capabilityId}")
    print(f"result: {result}")
    return SUCCESS


def runActivationPlanSanity() -> int:
    sink = DevTraceSink()

    repoRoot = getRepoRoot()
    planId = "bootstrap.plan"

    declaration = createActivationDeclaration(
        planId=planId,
        entries=(
            createActivationSpec(
                entryId="first-party.bootstrap.activation",
                ownerId="first-party.bootstrap",
                adapterKind=ActivationAdapterKind.PYTHON_IN_PROCESS,
                source=PurePosixPath("first-party/bootstrap/activation.py"),
                callableName="onLoad",
            ),
            createActivationSpec(
                entryId="first-party.bootstrapExtra.activation",
                ownerId="first-party.bootstrapExtra",
                adapterKind=ActivationAdapterKind.PYTHON_IN_PROCESS,
                source=PurePosixPath("first-party/bootstrapExtra/activation.py"),
                callableName="onLoad",
            ),
        ),
    )

    plan = materializeActivationPlan(
        declaration=declaration,
        basePath=repoRoot,
    )

    sink.emit(
        reason="ActivationPlanSanityStarted",
        message="activation plan sanity command started",
        attrs={
            "planId": planId,
            "entryCount": len(plan.entries),
        },
    )

    registry = CapabilityRegistry()
    adapters = {
        ActivationAdapterKind.PYTHON_IN_PROCESS: PythonInProcessAdapter(sink=sink),
    }

    report = activatePlan(
        plan=plan,
        registry=registry,
        adapters=adapters,
        sink=sink,
    )

    expectedEntryIds = tuple(entry.entryId for entry in plan.entries)
    if report.activatedEntryIds != expectedEntryIds:
        raise UsageError(f"Unexpected activated entry IDs: expected {expectedEntryIds!r}, got {report!r}.")

    echoCapabilityId = "bootstrap.activationEcho@1"
    reverseCapabilityId = "bootstrapExtra.reverse@1"

    if not registry.has(echoCapabilityId):
        raise UsageError(f"Missing expected capability: {echoCapabilityId}")

    if not registry.has(reverseCapabilityId):
        raise UsageError(f"Missing expected capability: {reverseCapabilityId}")

    echoResult = registry.call(echoCapabilityId, {"text": "hello"})
    reverseResult = registry.call(reverseCapabilityId, {"text": "hello"})

    if echoResult != {"text": "hello"}:
        raise UsageError(f"Unexpected echo result: expected {{'text': 'hello'}}, got {echoResult!r}.")

    if reverseResult != {"text": "olleh"}:
        raise UsageError(f"Unexpected reverse result: expected {{'text': 'olleh'}}, got {reverseResult!r}.")

    sink.emit(
        reason="ActivationPlanSanityCompleted",
        message="activation plan sanity command completed",
        attrs={
            "planId": report.planId,
            "entryCount": len(report.activatedEntries),
            "echoResult": echoResult,
            "reverseResult": reverseResult,
        },
    )

    print("Actant activation plan sanity OK")
    print(f"plan: {report.planId}")
    print("activated:")
    for activatedEntry in report.activatedEntries:
        print(f"  {activatedEntry.entryId}")
    print("registered:")
    print(f"  {echoCapabilityId}")
    print(f"  {reverseCapabilityId}")
    print("results")
    print(f"  {echoCapabilityId} -> {echoResult}")
    print(f"  {reverseCapabilityId} -> {reverseResult}")
    return SUCCESS


def runActivationFailureSanity() -> int:
    sink = DevTraceSink()

    repoRoot = getRepoRoot()
    planId = "bootstrap.failure-plan"
    entryId = "first-party.bootstrapBroken.activation"
    ownerId = "first-party.bootstrapBroken"

    declaration = createActivationDeclaration(
        planId=planId,
        entries=(
            createActivationSpec(
                entryId=entryId,
                ownerId=ownerId,
                adapterKind=ActivationAdapterKind.PYTHON_IN_PROCESS,
                source=PurePosixPath("first-party/bootstrapBroken/activation.py"),
                callableName="onLoad",
            ),
        ),
    )

    plan = materializeActivationPlan(
        declaration=declaration,
        basePath=repoRoot,
    )

    sink.emit(
        reason="ActivationFailureSanityStarted",
        message="activation failure sanity command started",
        attrs={
            "planId": planId,
            "entryId": entryId,
            "ownerId": ownerId,
        },
    )

    registry = CapabilityRegistry()
    adapters = {
        ActivationAdapterKind.PYTHON_IN_PROCESS: PythonInProcessAdapter(sink=sink),
    }

    try:
        activatePlan(
            plan=plan,
            registry=registry,
            adapters=adapters,
            sink=sink,
        )

    except ActivationError as err:
        if err.context.planId != planId:
            raise UsageError(f"Unexpected failure planId: {err.context.planId!r}") from err

        if err.context.entryId != entryId:
            raise UsageError(f"Unexpected failure entryId: {err.context.entryId!r}") from err

        if err.context.ownerId != ownerId:
            raise UsageError(f"Unexpected failure ownerId: {err.context.ownerId!r}") from err

        if err.context.adapterKind != ActivationAdapterKind.PYTHON_IN_PROCESS:
            raise UsageError(f"Unexpected failure adapterKind: {err.context.adapterKind!r}") from err

        if not isinstance(err.cause, RuntimeError):
            raise UsageError(f"Unexpected failure cause type: {typeName(err.cause)}") from err

        if str(err.cause) != "Intentional activation failure.":
            raise UsageError(f"Unexpected failure cause: {err.cause}") from err

        sink.emit(
            reason="ActivationFailureSanityCompleted",
            message="activation failure sanity command completed",
            attrs={
                "planId": err.context.planId,
                "entryId": err.context.entryId,
                "ownerId": err.context.ownerId,
                "adapterKind": err.context.adapterKind,
                "causeType": typeName(err.cause),
                "cause": str(err.cause),
            },
        )

        print("Actant activation failure sanity OK")
        print(f"plan: {err.context.planId}")
        print(f"failed: {err.context.entryId}")
        print(f"ownerId: {err.context.ownerId}")
        print(f"adapterKind: {err.context.adapterKind}")
        print(f"causeType: {typeName(err.cause)}")
        print(f"cause: {err.cause}")
        return SUCCESS

    raise UsageError("activation-failure-sanity expected ActivationError, but activation succeeded.")


def runActivationDeclarationSanity() -> int:
    sink = DevTraceSink()

    repoRoot = getRepoRoot()
    catalog = createContentRootCatalog(
        roots=(
            createContentRoot(
                rootId="repo",
                rootPath=repoRoot,
            ),
        ),
    )

    sources = createBootstrapActivationDeclarationSources()

    registry = CapabilityRegistry()
    adapters = {
        ActivationAdapterKind.PYTHON_IN_PROCESS: PythonInProcessAdapter(sink=sink),
    }

    batchReport = runActivationDeclarationSources(
        catalog=catalog,
        sources=sources,
        registry=registry,
        adapters=adapters,
        sink=sink,
    )

    echoCapabilityId = "bootstrap.activationEcho@1"
    reverseCapabilityId = "bootstrapExtra.reverse@1"

    if not registry.has(echoCapabilityId):
        raise UsageError(f"Missing expected capability: {echoCapabilityId}")

    if not registry.has(reverseCapabilityId):
        raise UsageError(f"Missing expected capability: {reverseCapabilityId}")

    echoResult = registry.call(echoCapabilityId, {"text": "hello"})
    reverseResult = registry.call(reverseCapabilityId, {"text": "hello"})

    if echoResult != {"text": "hello"}:
        raise UsageError(f"Unexpected echo result: expected {{'text': 'hello'}}, got {echoResult!r}.")

    if reverseResult != {"text": "olleh"}:
        raise UsageError(f"Unexpected reverse result: expected {{'text': 'olleh'}}, got {reverseResult!r}.")

    sink.emit(
        reason="ActivationDeclarationSanityCompleted",
        message="activation declaration sanity command completed",
        attrs={
            "entryCount": len(batchReport.runs),
            "planIds": tuple(run.activationReport.planId for run in batchReport.runs),
            "echoResult": echoResult,
            "reverseResult": reverseResult,
        },
    )

    print("Actant activation declaration sanity OK")
    print(f"sources: {len(batchReport.runs)}")

    for run in batchReport.runs:
        print(f"source: {run.source.rootId}:{run.source.declarationPath.as_posix()}")
        print(f"plan: {run.activationReport.planId}")
        print("activated:")
        for activatedEntry in run.activationReport.activatedEntries:
            print(f"  {activatedEntry.entryId}")

    print("registered:")
    print(f"  {echoCapabilityId}")
    print(f"  {reverseCapabilityId}")
    print("results")
    print(f"  {echoCapabilityId} -> {echoResult}")
    print(f"  {reverseCapabilityId} -> {reverseResult}")

    return SUCCESS
