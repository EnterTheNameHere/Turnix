# file: tests/test_setupBat.py ; version: 1
"""Exercise the actual Windows batch/PowerShell boundary without installing packages."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows setup entry point")


@pytest.mark.parametrize("exitCode,pauseOnFailure", [(0, False), (37, False), (37, True)])
def testWrapperForwardsArgumentsAndExitFromUnrelatedDirectory(tmp_path, exitCode, pauseOnFailure):
    """A spaced checkout must invoke its adjacent script and preserve native status."""
    checkout = tmp_path / "checkout with spaces"
    caller = tmp_path / "unrelated caller"
    checkout.mkdir()
    caller.mkdir()
    wrapper = checkout / "setup.bat"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "setup.bat", wrapper)
    (checkout / "setup.ps1").write_text(
        'param([string] $Requirements, [int] $RequestedExit)\n'
        '$ErrorActionPreference = "Stop"\n'
        'Get-Command Get-FileHash -ErrorAction Stop | Out-Null\n'
        'Write-Output "ADJACENT_SETUP=$PSScriptRoot"\n'
        'Write-Output "REQUIREMENTS=$Requirements"\n'
        'Write-Output "CALLER=$((Get-Location).Path)"\n'
        'exit $RequestedExit\n', encoding="utf-8",
    )
    harness = caller / "invoke.cmd"
    harness.write_text(
        f'@echo off\ncall "{wrapper}" -Requirements "custom requirements.txt" -RequestedExit {exitCode}\n'
        'exit /b %ERRORLEVEL%\n', encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("ACTANT_SETUP_NO_PAUSE", None)
    if not pauseOnFailure:
        environment["ACTANT_SETUP_NO_PAUSE"] = "1"
    result = subprocess.run(
        [str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"), "/d", "/c", str(harness)],
        cwd=caller, env=environment, input="\n", capture_output=True,
        text=True, errors="replace", timeout=30, check=False,
    )
    assert result.returncode == exitCode, result.stdout + result.stderr
    assert f"ADJACENT_SETUP={checkout}" in result.stdout
    assert "REQUIREMENTS=custom requirements.txt" in result.stdout
    assert f"CALLER={caller}" in result.stdout
    if exitCode:
        assert f"Actant internal setup failed with exit code {exitCode}." in result.stdout
    else:
        assert "setup failed" not in result.stdout
