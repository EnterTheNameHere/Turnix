# file: backend/cli/main.py
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from backend.capabilities.registry import CapabilityRegistry
from backend.cli.activationSanity import (
    runActivationDeclarationSanity,
    runActivationFailureSanity,
    runActivationPlanSanity,
    runActivationSanity,
)
from backend.context.modCallContext import ModCallContext
from backend.core.errors import UsageError
from backend.core.paths import getRepoRoot
from backend.tracing.devTrace import DevTraceSink

if TYPE_CHECKING:
    from collections.abc import Sequence

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
  python -m backend.cli.main activation-declaration-sanity

Commands:
  sanity
    Verify that the backend package can run under embedded Python.

  capability-sanity
    Verify minimal capability registration and invocation.

  activation-sanity
    Verify path-based Python activation loading.

  activation-plan-sanity
    Verify ordered manual activation plan loading.

  activation-failure-sanity
    Verify activation failure wrapping.

  activation-declaration-sanity
    Verify loading temporary activation declaration file input.

""",
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

        if command == "activation-declaration-sanity":
            if len(args) != 1:
                raise UsageError("activation-declaration-sanity command takes no arguments.")
            return runActivationDeclarationSanity()

        raise UsageError(f"Unknown command: {command}")

    except UsageError as err:
        print(f"Error: {err}", file=sys.stderr)
        return USAGE_ERROR

    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
