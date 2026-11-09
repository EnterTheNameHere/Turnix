# file: tests/first_party/experimentRunner/test_usability.py ; version: 2
"""Protect private setup, maintained preflight, real Windows launch and offline text output."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.first_party.experimentRunner.test_experimentRunner import rig, stream
from tests.first_party.experimentRunner.test_procedure import definition, host5, syntheticCli

setup = importlib.import_module("first-party.applications.experimentRunner.setupLocal")
launcher = importlib.import_module("first-party.applications.experimentRunner.launcher")
readable = importlib.import_module("first-party.applications.experimentRunner.readable")
APP = setup.APP
ROOT = setup.ROOT


def testSetupPreservesEveryExistingPrivateFile(tmp_path, monkeypatch):
    """Only absent templates are created, even when existing user contents are invalid."""
    app = tmp_path / "Application with spaces"
    shutil.copytree(APP / "examples", app / "examples")
    shutil.copyfile(APP / "run.bat", app / "run.bat")
    monkeypatch.setattr(setup, "APP", app)
    tools = {"tools": {name: {"executable": str(tmp_path / (name + ".exe"))} for name in ("ruff", "ty")}}
    setup.initialize(tools)
    local = app / "local"
    assert (local / "run.bat").read_bytes() == (APP / "run.bat").read_bytes()
    assert json.loads((local / "host.json").read_text())["processTools"]["ty"] == tools["tools"]["ty"]["executable"]
    for name in ("run.bat", "host.json", "host.basic.json", "experiments/user.json", "results/user.txt"):
        (local / name).write_bytes(b"private user bytes\x00")
    before = {p.relative_to(local): p.read_bytes() for p in local.rglob("*") if p.is_file()}
    setup.initialize(tools)
    assert before == {p.relative_to(local): p.read_bytes() for p in local.rglob("*") if p.is_file()}


def testSetupFailureCategoriesDoNotInitializeLocal(monkeypatch, tmp_path, capsys):
    """A platform check failure does not fall through to filesystem initialization."""
    monkeypatch.setattr(setup, "APP", tmp_path)

    def unavailable():
        """Represent an unavailable import/tool prerequisite."""
        raise ImportError("private unavailable path")

    monkeypatch.setattr(setup, "prerequisites", unavailable)
    assert setup.main() == 2
    output = capsys.readouterr().err
    assert "ROOT setup.bat separately" in output and "private unavailable path" not in output
    assert not (tmp_path / "local").exists()
    monkeypatch.setattr(setup, "prerequisites", lambda: {})
    assert setup.main() == 1
    assert "local setup failed" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["basic-conversation", "python-materialization"])
def testMaintainedExamplesPassPreflightWithExplicitFixtureAssets(kind, rig):
    """All authored inputs and budgets validate without any inference or native tool launch."""
    state, assets, results = rig
    value = json.loads((APP / "examples" / kind / "host.template.json").read_text())
    value["llamaCpp"]["executable"] = assets["llamaCpp"]["executable"]
    value["llamaCpp"]["models"]["Q4"]["modelPath"] = assets["llamaCpp"]["models"]["Q"]["modelPath"]
    if kind == "python-materialization":
        value["processTools"] = {"ruff": str(Path(sys.executable)), "ty": str(Path(sys.executable))}
    source = launcher.procedureInputs.readDefinition(APP / "examples" / kind / "definition.json", "json")
    prepared = launcher.prepare(source, value)
    assert prepared[0].value()["experimentId"]
    assert state["processes"] == []
    if kind == "python-materialization":
        steps = prepared[0].value()["procedure"]
        assert [s["op"] for s in steps] == ["message", "inference", "save", "cli", "cli", "cli", "cli", "message", "inference"]
        assert prepared[2].procedureInputs.data()["task"] == (APP / "examples" / kind / "task.txt").read_bytes()


def embeddedFixture(checkout):
    """Copy only interpreter/stdlib binaries for actual PATH-free native batch tests."""
    runtime = checkout / "python-embedded"
    runtime.mkdir(parents=True)
    installed = Path(sys.executable).parent
    for path in installed.iterdir():
        if path.is_file() and (path.suffix.lower() in (".dll", ".zip", ".pyd") or path.name == "python.exe"):
            shutil.copyfile(path, runtime / path.name)
    tag = f"python{sys.version_info.major}{sys.version_info.minor}"
    (runtime / (tag + "._pth")).write_text(f"{tag}.zip\n.\nimport site\n", encoding="utf-8")
    return runtime


@pytest.mark.skipif(sys.platform != "win32", reason="Windows BAT entrypoints")
@pytest.mark.parametrize("local,exitCode", [(False, 0), (True, 37)])
def testEditableBatUsesAdjacentRootPythonAndPropagatesStatus(tmp_path, local, exitCode):
    """Actual cmd runs a spaced checkout from unrelated cwd with PATH empty."""
    checkout = tmp_path / "checkout with spaces"
    runtime = embeddedFixture(checkout)
    app = checkout / "first-party/applications/experimentRunner"
    (app / "local").mkdir(parents=True)
    target = app / "local/run.bat" if local else app / "run.bat"
    target.write_bytes((APP / "run.bat").read_bytes())
    output = checkout / "arguments.json"
    (app / "run.py").write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(output)!r}).write_text(json.dumps(dict(executable=sys.executable, argv=sys.argv, cwd=str(pathlib.Path.cwd()))))\n"
        f"raise SystemExit({exitCode})\n", encoding="utf-8")
    environment = dict(os.environ, PATH="")
    result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"), "/d", "/c", str(target)],
                            cwd=tmp_path, env=environment, capture_output=True, timeout=30)
    assert result.returncode == exitCode, result.stdout + result.stderr
    observed = json.loads(output.read_text())
    assert Path(observed["executable"]) == runtime / "python.exe"
    assert Path(observed["cwd"]) == app
    args = observed["argv"]
    assert Path(args[args.index("--definition") + 1]) == app / "examples/basic-conversation/definition.json"
    assert Path(args[args.index("--host-config") + 1]) == app / "local/host.basic.json"
    assert Path(args[args.index("--results") + 1]) == app / "local/results"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows setup entrypoints")
@pytest.mark.parametrize("runtimePresent", [False, True])
def testStandaloneSetupMissingRootDoesNotInstallIt(tmp_path, runtimePresent):
    """Missing runtime or unavailable Actant imports fail before local initialization."""
    checkout = tmp_path / "missing runtime checkout"
    app = checkout / "first-party/applications/experimentRunner"
    app.mkdir(parents=True)
    for name in ("setup.bat", "setup.ps1", "setupLocal.py"):
        shutil.copyfile(APP / name, app / name)
    if runtimePresent:
        embeddedFixture(checkout)
    (checkout / "setup.ps1").write_text('throw "Root setup must not run"', encoding="utf-8")
    result = subprocess.run([os.environ["COMSPEC"], "/d", "/c", str(app / "setup.bat")],
                            cwd=tmp_path, env=dict(os.environ, PATH=""), capture_output=True, timeout=30)
    assert result.returncode == 2
    assert b"ROOT setup.bat separately" in result.stdout
    assert not (app / "local").exists()
    assert (checkout / "python-embedded").exists() is runtimePresent


@pytest.mark.parametrize("failedExtract", [False, True])
def testReadableArchiveStagesArePrivateInertAndNeverOverwrite(rig, monkeypatch, failedExtract):
    """Actual synthetic runtime retains byte-changing source and all fixed CLI feedback."""
    state, assets, results = rig
    syntheticCli(monkeypatch, state)
    state["streams"] = [stream("no Python fence" if failedExtract else "```python\nimport os\nx=1\n```"), stream("```python\nx = 2\n```")]
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host5(assets)), results)
    value = snapshot.value()
    archive = Path(value["archive"]["path"])
    before = {p.relative_to(archive): p.read_bytes() for p in archive.rglob("*") if p.is_file()}
    unit = value["suite"]["units"][0]["unitId"]
    output = readable.exportText(archive, unit, results / "exports", "readable-01")
    directory = Path(output["path"])
    manifest = json.loads((directory / "manifest.json").read_text())
    expected = definition()["procedure"][:3] if failedExtract else definition()["procedure"]
    assert [r["payload"]["opId"] for r in manifest["records"] if r["kind"] == "step-planned"] == [s["id"] for s in expected]
    texts = [(directory / a["file"]).read_text(encoding="utf-8") for a in manifest["artifacts"]]
    assert any(text == ("no Python fence" if failedExtract else "```python\nx = 2\n```") for text in texts)
    if not failedExtract:
        assert any(text == "x = 1\n" for text in texts)
        assert any("diagnostic" in text for text in texts)
    assert all(p.suffix in (".txt", ".json") for p in directory.iterdir())
    assert ("final" in (directory / "index.txt").read_text()) is not failedExtract
    assert manifest["contentPolicy"] == "selected-private"
    with pytest.raises(FileExistsError):
        readable.exportText(archive, unit, results / "exports", "readable-01")
    for name in ("../escape", "C:/escape", "..", "a/b", ""):
        with pytest.raises(readable.reporting.ReportError):
            readable.exportText(archive, unit, results / "exports", name)
    for root, name in ((archive, "inside"), (archive.parent.parent, archive.parent.name)):
        with pytest.raises(readable.reporting.ReportError):
            readable.exportText(archive, unit, root, name)
    assert before == {p.relative_to(archive): p.read_bytes() for p in archive.rglob("*") if p.is_file()}
