# file: tests/test_setupTools.py ; version: 1
"""Protect shared setup's tool pin and native failure boundaries."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    "setupToolVerification", Path(__file__).resolve().parents[1] / "scripts/verify_tools.py"
)
assert SPEC is not None and SPEC.loader is not None
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


@pytest.mark.parametrize("contents", [
    "ruff==1\n", "ruff>=1\nty==2\n", "ruff==1\nruff==1\nty==2\n",
    "ruff==1\nty==\n", "ruff==1\nty==2\nextra==3\n",
])
def testRejectIncompleteOrAmbiguousPins(tmp_path, contents):
    """A malformed central manifest must fail instead of resolving arbitrary versions."""
    manifest = tmp_path / "pins.txt"
    manifest.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        TOOLS.pinnedVersions(manifest)


def testCentralManifestIsExactlyPinned():
    """Both tools must use the reviewed recipe-compatible versions."""
    manifest = Path(__file__).resolve().parents[1] / "requirements-tools.txt"
    assert TOOLS.pinnedVersions(manifest) == {"ruff": "0.17.4", "ty": "0.0.99"}


def testNativeDiagnosticFailureIsSetupFailure(monkeypatch, tmp_path):
    """Nonzero diagnostic exits must prevent successful setup verification."""
    def fail(arguments, **kwargs):
        """Simulate native usage failure and retain its diagnostic output."""
        assert arguments == [str(tmp_path / "ty.exe"), "check"]
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(arguments, 2, "", "invalid usage")

    monkeypatch.setattr(TOOLS.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="invalid usage"):
        TOOLS.checked(tmp_path / "ty.exe", ["check"], tmp_path)


def testNativeTimeoutIsSetupFailure(monkeypatch, tmp_path):
    """A stuck tool cannot be treated as usable."""
    def timeout(arguments, **kwargs):
        """Raise the same timeout the native subprocess boundary exposes."""
        raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])

    monkeypatch.setattr(TOOLS.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        TOOLS.checked(tmp_path / "ruff.exe", ["--version"], tmp_path)
