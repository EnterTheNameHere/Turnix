# file: backend/cli/main.py
from __future__ import annotations

import sys
from collections.abc import Sequence

from backend.activation.activationAdapterKind import ActivationAdapterKind
from backend.activation.activationEntry import createPythonActivationEntry
from backend.activation.activationErrors import ActivationError
from backend.activation.activationPlan import createActivationPlan
from backend.activation.activator import activatePlan
from backend.adapters.pythonInProcess import PythonInProcessAdapter
from backend.capabilities.registry import CapabilityRegistry
from backend.context.modCallContext import ModCallContext
from backend.core.errors import UsageError
from backend.core.paths import getRepoRoot
from backend.tracing.devTrace import DevTraceSink


SUCCESS = 0
USAGE_ERROR = 2
INTERNAL_ERROR = 70


def printHelp() -> None:
    print("""Actant backend command surface

Usage:
  python -m backend.cli.main sanity
  python -m backend.cli.main capability-sanity
  python -m backend.cli.main activation-sanity
  python -m backend.cli.main activation-plan-sanity
  python -m backend.cli.main activation-failure-sanity

Commands:
  sanity                    Verify that the backend package can run under
                              embedded Python.
  capability-sanity         Verify minimal capability registration and
                              invocation.
  activation-sanity         Verify path-based Python activation loading.

  activation-plan-sanity    Verify ordered manual activation plan loading.

  activation-failure-sanity Verify activation failure wrapping.

"""
    )


def runSanity() -> int:
    sink = DevTraceSink()

    repoRoot = getRepoRoot()

    sink.emit(
        reason="BackendSanityStarted",
        message="backend sanity command started",
        attrs={
            "repoRoot": str(repoRoot),
            "pythonVersion": sys.version.replace("\n", " "),
        },
    )

    if sys.version_info < (3, 12):
        raise UsageError(
            "Python version 3.12 or newer is required. ",
            f"Current version: {sys.version.split()[0]}."
        )

    setupPath = repoRoot / "setup.ps1"
    if not setupPath.exists():
        raise UsageError(f"Repository root sanity failed: missing {setupPath}")

    backendPath = repoRoot / "backend"
    if not backendPath.exists():
        raise UsageError(f"Repository root sanity failed: missing {backendPath}")

    sink.emit(
        reason="BackendSanityCompleted",
        message="backend sanity command completed",
        attrs={
            "repoRoot": str(repoRoot),
            "setupPath": str(setupPath),
            "backendPath": str(backendPath),
        },
    )

    print("Actant backend sanity OK")
    print(f"repoRoot: {repoRoot}")
    print("diagnostics: dev sink active")
    return SUCCESS


def runCapabilitySanity() -> int:
    sink = DevTraceSink()

    ownerId = "bootstrap"
    capabilityId = "bootstrap.echo@1"

    sink.emit(
        reason="CapabilitySanityStarted",
        message="capability sanity command started",
        attrs={
            "ownerId": ownerId,
            "capabilityId": capabilityId,
        },
    )

    registry = CapabilityRegistry()
    ctx = ModCallContext(
        ownerId=ownerId,
        capabilityRegistry=registry,
    )

    def echo(payload: object | None) -> object:
        if not isinstance(payload, dict):
            raise UsageError(f"echo payload must be a dict, got {type(payload)}")

        text = payload.get("text")
        if not isinstance(text, str):
            raise UsageError(f"echo payload must contain 'text' key, got {payload}")

        return {"text": text}

    ctx.registerCapability(capabilityId, echo)

    sink.emit(
        reason="CapabilityRegistered",
        message="capability registered through ModCallContext",
        attrs={
            "ownerId": ownerId,
            "capabilityId": capabilityId,
        },
    )

    result = registry.call(capabilityId, {"text": "hello"})

    if result != {"text": "hello"}:
        raise UsageError(f"Unexpected capability result: expected {{'text': 'hello'}}, got {result!r}")

    sink.emit(
        reason="CapabilitySanityCompleted",
        message="capability sanity command completed",
        attrs={
            "ownerId": ownerId,
            "capabilityId": capabilityId,
            "result": result,
        },
    )

    print("Actant capability sanity OK")
    print(f"registered: {capabilityId}")
    print(f"result: {result}")
    return SUCCESS


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

    firstEntry = createPythonActivationEntry(
        entryId="first-party.bootstrap.activation",
        ownerId="first-party.bootstrap",
        sourcePath=repoRoot / "first-party" / "bootstrap" / "activation.py",
        callableName="onLoad",
    )

    secondEntry = createPythonActivationEntry(
        entryId="first-party.bootstrapExtra.activation",
        ownerId="first-party.bootstrapExtra",
        sourcePath=repoRoot / "first-party" / "bootstrapExtra" / "activation.py",
        callableName="onLoad",
    )

    plan = createActivationPlan(
        planId=planId,
        entries=(firstEntry, secondEntry),
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
            "planId": planId,
            "entryCount": len(plan.entries),
            "echoResult": echoResult,
            "reverseResult": reverseResult,
        },
    )

    print("Actant activation plan sanity OK")
    print(f"plan: {planId}")
    print("activated:")
    for activatedEntry in report.activatedEntries:
        print(f"  {activatedEntry}")
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

    entry = createPythonActivationEntry(
        entryId=entryId,
        ownerId=ownerId,
        sourcePath=repoRoot / "first-party" / "bootstrapBroken" / "activation.py",
        callableName="onLoad",
    )

    plan = createActivationPlan(
        planId=planId,
        entries=(entry,),
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
            raise UsageError(f"Unexpected failure cause type: {type(err.cause).__name__}") from err

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
                "causeType": type(err.cause).__name__,
                "cause": str(err.cause),
            },
        )

        print("Actant activation failure sanity OK")
        print(f"plan: {err.context.planId}")
        print(f"failed: {err.context.entryId}")
        print(f"ownerId: {err.context.ownerId}")
        print(f"adapterKind: {err.context.adapterKind}")
        print(f"causeType: {type(err.cause).__name__}")
        print(f"cause: {err.cause}")
        return SUCCESS

    raise UsageError("activation-failure-sanity expected ActivationError, but activation succeeded.")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        printHelp()
        return SUCCESS

    command = args[0].lower()

    try:
        if command == "sanity":
            if len(args) != 1:
                raise UsageError("sanity command takes no arguments.")
            return runSanity()

        if command == "capability-sanity":
            if len(args) != 1:
                raise UsageError("capability-sanity command takes no arguments.")
            return runCapabilitySanity()

        if command == "activation-sanity":
            if len(args) != 1:
                raise UsageError("activation-sanity command takes no arguments.")
            return runActivationSanity()

        if command == "activation-plan-sanity":
            if len(args) != 1:
                raise UsageError("activation-plan-sanity command takes no arguments.")
            return runActivationPlanSanity()

        if command == "activation-failure-sanity":
            if len(args) != 1:
                raise UsageError("activation-failure-sanity command takes no arguments.")
            return runActivationFailureSanity()

        raise UsageError(f"Unknown command: {command}")

    except UsageError as err:
        print(f"Error: {err}", file=sys.stderr)
        return USAGE_ERROR

    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
