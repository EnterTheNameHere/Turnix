# file: backend/cli/main.py
from __future__ import annotations

import sys
from collections.abc import Sequence

from backend.core.errors import UsageError
from backend.core.paths import getRepoRoot
from backend.tracing.devTrace import DevTraceSink


SUCCESS = 0
USAGE_ERROR = 2
INTERNAL_ERROR = 70


def printHelp() -> None:
    print("""
Actant backend command surface

Usage:
  python -m backend.cli.main sanity

Commands:
  sanity  Verify that the backend package can run under embedded Python.
""")


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

        raise UsageError(f"Unknown command: {command}")

    except UsageError as err:
        print(f"Error: {err}", file=sys.stderr)
        return USAGE_ERROR

    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
