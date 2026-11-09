# file: scripts/verify_tools.py ; version: 1
"""Verify platform-pinned tools in the selected Windows embedded Python environment."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def pinnedVersions(requirements: Path) -> dict[str, str]:
    """Read the central exact-version manifest, rejecting ambiguous tool definitions."""
    versions: dict[str, str] = {}
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        if not separator or name not in {"ruff", "ty"} or not version or name in versions:
            raise ValueError(f"Invalid shared tool pin: {line!r}")
        versions[name] = version
    if set(versions) != {"ruff", "ty"}:
        raise ValueError("Both Ruff and ty must be pinned")
    return versions


def checked(executable: Path, arguments: list[str], cwd: Path) -> str:
    """Run an absolute environment-owned tool and report bounded setup failure context."""
    result = subprocess.run(
        [str(executable), *arguments], cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{executable.name} {arguments!r} failed ({result.returncode}):\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout.strip()


def verifyTools() -> dict[str, object]:
    """Check metadata, actual native launchers and isolated diagnostics without repository lint."""
    if sys.platform != "win32":
        raise RuntimeError("Root embedded-Python setup currently supports Windows only")
    root = Path(__file__).resolve().parent.parent
    scripts = Path(sys.executable).resolve().parent / "Scripts"
    versions = pinnedVersions(root / "requirements-tools.txt")
    tools: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="actant-tool-check-") as temporary:
        directory = Path(temporary)
        sample = directory / "sample.py"
        sample.write_text('value: int = 1\n', encoding="utf-8")
        for name, version in versions.items():
            executable = scripts / (name + ".exe")
            if not executable.is_file():
                raise RuntimeError(f"Required local launcher is missing: {executable}")
            installed = importlib.metadata.version(name)
            actual = checked(executable, ["--version"], directory)
            if installed != version or actual.split()[:2] != [name, version]:
                raise RuntimeError(
                    f"{name} version mismatch: expected {version}, metadata={installed}, native={actual!r}"
                )
            if name == "ruff":
                arguments = ["check", "--isolated", "--no-cache", "--select", "E4,E7,E9,F", str(sample)]
            else:
                arguments = ["check", "--config-file", "NUL", "--output-format", "concise", str(sample)]
            checked(executable, arguments, directory)
            tools[name] = {"executable": str(executable), "version": version, "nativeVersion": actual,
                           "verifiedArguments": arguments}
    return {"python": sys.version.split()[0], "pythonExecutable": sys.executable, "tools": tools}


def main() -> None:
    """Print the verified runtime/tool contract or exit unsuccessfully with the original error."""
    print(json.dumps(verifyTools(), indent=2))


if __name__ == "__main__":
    main()
