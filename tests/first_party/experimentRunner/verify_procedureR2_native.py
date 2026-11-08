# file: tests/first_party/experimentRunner/verify_procedureR2_native.py ; version: 1
"""Explicit opt-in native Windows recipe gate; uses existing verified copies, no installation/model."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess

from backend.process.context import ProcessFacade
from backend.workspace.context import WorkspaceFacade
from tests.first_party.experimentRunner.test_archive import REAL_POPEN
from tests.first_party.experimentRunner.test_procedure import rig, definition, host5, syntheticCli, launcher, reader
from tests.first_party.experimentRunner.test_experimentRunner import stream

TOOLS = {
    "ruff": ("python-embedded/ex06-tools-local/ruff-0.17.4/ruff-0.17.4.data/scripts/ruff.exe",
             "0e71094b51a4241cb725e76e6375c55c8433dd06ec380ab987c970edd1618565"),
    "ty": ("python-embedded/ex06-tools-local/ty-0.0.99/ty-0.0.99.data/scripts/ty.exe",
           "68a68da441e903bb1330ba769fd47278d60fe12fb7fb9b00220b87a516ebb35b")}


def test_native_recipes_and_real_process_facade(rig, monkeypatch, tmp_path):
    """Verify native config isolation/exits and one actual four-tool pass with synthetic model IO."""
    assert os.name == "nt", "This explicit native gate verifies Windows only."
    executables, tools, checks = {}, {}, []
    for name, (relative, digest) in TOOLS.items():
        path = launcher.ROOT / relative
        assert path.is_file(), "Expected existing isolated native copy; no installation is performed."
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        executables[name] = str(path)
        tools[name] = dict(workspaceExecutable=relative, exeSha256=digest)

    owned = tmp_path / "native-cases"
    owned.mkdir()
    source = owned / "source.py"
    source.write_bytes(b'answer: int = 1\nraise RuntimeError("SOURCE_EXECUTION_FORBIDDEN")\n')

    def native(name, arguments, expected, label):
        """Run a finite owned native check and retain bounded sanitized provenance."""
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", REAL_POPEN)
            result = subprocess.run([executables[name], *arguments], cwd=owned,
                                    capture_output=True, timeout=10)
        assert result.returncode == expected, (label, result.stdout, result.stderr)
        assert len(result.stdout) + len(result.stderr) <= 262144
        checks.append(dict(label=label, tool=name,
            arguments=["SOURCE" if a == str(source) else a for a in arguments], exitCode=result.returncode,
            stdoutBytes=len(result.stdout), stderrBytes=len(result.stderr),
            stdoutSha256=hashlib.sha256(result.stdout).hexdigest(), stderrSha256=hashlib.sha256(result.stderr).hexdigest()))
        return result

    for name in executables:
        version = native(name, ["--version"], 0, name + "-version").stdout.decode("utf-8").strip()
        tools[name]["version"] = version
    assert tools["ruff"]["version"].startswith("ruff 0.17.4")
    assert tools["ty"]["version"].startswith("ty 0.0.99")
    helpText = native("ty", ["check", "--help"], 0, "ty-help").stdout.decode("utf-8")
    assert "--config-file" in helpText and "--isolated" not in helpText
    legacy = native("ty", ["check", "--isolated", "--output-format", "concise", str(source)], 2, "legacy-usage-error")
    assert b"--isolated" in legacy.stderr
    current = ["check", "--config-file", "NUL", "--output-format", "concise", str(source)]
    native("ty", current, 0, "empty-config-valid")
    invalidConfig = b"[tool.ty]\nnot_a_real_key = true\n"
    (owned / "pyproject.toml").write_bytes(invalidConfig)
    native("ty", ["check", "--output-format", "concise", str(source)], 2, "ambient-invalid-config")
    native("ty", current, 0, "empty-config-ignores-ambient")
    source.write_bytes(b'answer: int = "bad"\n')
    wrong = native("ty", current, 1, "true-type-diagnostic")
    assert b"invalid-assignment" in wrong.stdout + wrong.stderr

    state, host, destination = rig
    # Only model transport/managed server stay synthetic; restore real configured CLI execution.
    actualExecute = ProcessFacade.execute
    syntheticCli(monkeypatch, state)
    state["streams"][0] = stream('```python\nx=1\nraise RuntimeError("SOURCE_EXECUTION_FORBIDDEN")\n```\n')
    host = host5(host)
    host["processTools"] = executables
    host["archive"]["hashAssets"] = True
    host["procedureLimits"]["messageBytes"] = 131072
    for command in host["approvedCommands"].values():
        command.update(stdoutBytes=256, stderrBytes=256, timeoutSeconds=10)
    calls = []
    def execute(self, ticket, *, beforeLaunch):
        """Use actual shared ACK/spawn/capture/cleanup for the four owned native tool calls."""
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", REAL_POPEN)
            result = actualExecute(self, ticket, beforeLaunch=beforeLaunch)
        calls.append(dict(tool=result.request.toolName, arguments=["SOURCE" if a == result.request.arguments[-1] else a
            for a in result.request.arguments], status=result.status, exitCode=result.exitCode,
            cleanup=result.cleanup.disposition, stdoutBytes=len(result.stdout.data), stderrBytes=len(result.stderr.data)))
        return result
    materialize = WorkspaceFacade.materializeBytes
    def write(self, relativePath, raw):
        """Create an owned invalid ambient config through the same mediated test workspace."""
        path = materialize(self, relativePath, raw)
        if str(relativePath) == "src/main.py":
            materialize(self, "pyproject.toml", invalidConfig)
        return path
    monkeypatch.setattr(ProcessFacade, "execute", execute)
    monkeypatch.setattr(WorkspaceFacade, "materializeBytes", write)
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host), destination, handleInterrupt=False)
    value = snapshot.value()
    assert launcher.exitCode(value) == 0, value
    assert state["calls"] == 2 and len(calls) == 4
    assert all(c["status"] == "completed" and c["cleanup"] == "confirmed" and c["exitCode"] == 0 for c in calls)
    assert calls[2]["arguments"] == ["check", "--config-file", "NUL", "--output-format", "concise", "SOURCE"]
    view = reader.inspectArchive(value["archive"]["path"])
    assert view["status"] == "sealed" and view["recipeProvenance"] == {"ty-check": "windows-empty-config"}
    report = importlib.import_module("first-party.applications.experimentRunner.reporting").report(value["archive"]["path"])
    assert len(report["cliMetrics"]) == 4 and report["metrics"][0]["contributing"] == 2
    evidence = dict(schemaId="actant.experiment.ex06-r2-native-verification@1", platform="Windows-amd64",
        tools=tools, checks=checks, runtime=dict(modelTransport="synthetic", managedServer="synthetic",
            configuredProcess="actual-native-shared-facade", inferenceCalls=state["calls"], toolCalls=calls,
            archiveStatus=view["status"], suiteStatus=value["suite"]["status"], ambientInvalidConfig=True,
            sourceContainsExecutionTrap=True),
        limits=dict(sourceExecuted=False, modelLaunched=False, globalInstallOrPathChange=False,
                    posixVariantVerified=False, hashesAreObservationsNotAttestation=True))
    if output := os.environ.get("TURNIX_EX06_R2_EVIDENCE"):
        Path(output).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
