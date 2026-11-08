# file: tests/first_party/experimentRunner/test_procedureR2.py ; version: 1
"""Closed Windows ty recipe, historical archive evidence and usage-error regression checks."""
import copy
import importlib
from pathlib import Path
from types import SimpleNamespace
import time
from dataclasses import replace

import pytest

from backend.process.context import ProcessFacade
from tests.first_party.experimentRunner.test_procedure import rig, definition, host5, syntheticCli, launcher, pf, reader

NEW = ["check", "--config-file", "NUL", "--output-format", "concise", {"artifact": True}]
OLD = ["check", "--isolated", "--output-format", "concise", {"artifact": True}]


def test_exact_recipe_and_example(rig):
    """Current host/example match the reviewed argv and retain true diagnostic exits only."""
    assert pf.RECIPES["ty-check"] == ("ty", NEW, [0, 1], False)
    host = launcher.hostConfiguration(host5(rig[1])).value()
    example = launcher.readJson(launcher.APP / "host5.example.json")
    for value in (host, example):
        assert value["approvedCommands"]["ty-check"]["arguments"] == NEW
        assert value["approvedCommands"]["ty-check"]["allowedExitCodes"] == [0, 1]


@pytest.mark.parametrize("arguments", [OLD, ["check", "--output-format", "concise", {"artifact": True}],
    ["check", "--config-file", "arbitrary.toml", "--output-format", "concise", {"artifact": True}],
    ["check", "--config-file", "/dev/null", "--output-format", "concise", {"artifact": True}],
    ["check", "--config-file", "nul", "--output-format", "concise", {"artifact": True}]],
    ids=["legacy-isolated", "ambient-discovery", "arbitrary-config", "unverified-posix", "device-alias"])
def test_current_host_refuses_unreviewed_recipe(rig, arguments):
    """No historical toggle, config selection or unrestricted argv is available to a host definition."""
    host = host5(rig[1])
    host["approvedCommands"]["ty-check"]["arguments"] = arguments
    with pytest.raises(ValueError):
        launcher.prepare(definition(), host)
    assert not rig[0]["processes"] and rig[0]["calls"] == 0


def test_no_usage_error_whitelist(rig):
    """A host cannot promote ty usage exit2 to normal diagnostics."""
    host = host5(rig[1])
    host["approvedCommands"]["ty-check"]["allowedExitCodes"] = [0, 1, 2]
    with pytest.raises(ValueError):
        launcher.prepare(definition(), host)


@pytest.mark.parametrize("path", ["NUL", "nul.py", "source/CON", "COM1.py"])
def test_host_empty_config_does_not_allow_authored_devices(path):
    """The fixed host-owned NUL config token grants no artifact path authority."""
    with pytest.raises(ValueError):
        pf.path(path)


def test_windows_only_execution_but_portable_historical_read(rig, monkeypatch):
    """Reading exact Windows recipes on another platform never opens their device/config path."""
    host = host5(rig[1])
    monkeypatch.setattr(pf, "sys", SimpleNamespace(platform="linux"))
    with pytest.raises(ValueError):
        pf.host(host)
    pf.host(host, archived=True)
    host["approvedCommands"]["ty-check"]["arguments"] = OLD
    pf.host(host, archived=True)
    host["approvedCommands"]["ty-check"]["arguments"] = ["check", "arbitrary"]
    with pytest.raises(ValueError):
        pf.host(host, archived=True)


def test_current_argv_and_usage_failure_archive(rig, monkeypatch):
    """A real-shaped usage failure remains completed exit2/failed step, never correction feedback."""
    calls = syntheticCli(monkeypatch, rig[0])
    actual = ProcessFacade.execute
    def execute(self, ticket, *, beforeLaunch):
        """Inject the native usage exit at the otherwise unchanged synthetic tool boundary."""
        value = actual(self, ticket, beforeLaunch=beforeLaunch)
        return replace(value, exitCode=2) if ticket.request.toolName == "ty" else value
    monkeypatch.setattr(ProcessFacade, "execute", execute)
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    value = snapshot.value()
    assert launcher.exitCode(value) == 1 and rig[0]["calls"] == 1 and len(calls) == 3
    argv = calls[-1]["arguments"]
    assert argv[:-1] == NEW[:-1] and Path(argv[-1]).is_absolute()
    view = reader.inspectArchive(value["archive"]["path"], includeContent=True)
    assert view["status"] == "sealed" and view["recipeProvenance"] == {"ty-check": "windows-empty-config"}
    assert next(s for s in view["procedureSteps"] if s["opId"] == "ty-check")["reason"] == "unexpected-exit"
    outputs = [c["value"] for c in view["content"] if type(c["value"]) is dict
        and c["value"].get("schemaId") == pf.PREFIX + "process-outcome@1"]
    assert outputs[-1]["status"] == "completed" and outputs[-1]["exitCode"] == 2


def test_historical_archive_recipe_is_read_only_evidence(rig, tmp_path, monkeypatch):
    """Reconstruct an inert pre-R2 plan and preserve its exact legacy policy without execution."""
    prepared = launcher.prepare(definition(), host5(rig[1]))
    definitionValue, host, composer = prepared
    legacy = host.value()
    legacy["approvedCommands"]["ty-check"]["arguments"] = copy.deepcopy(OLD)
    runType = importlib.import_module("first-party.applications.experimentRunner.procedureArchive").Run
    suite = launcher.results.initialSuite(definitionValue, launcher.schema.unitDefinition)
    writer = launcher.archive.ArchiveWriter(tmp_path / "historical", suite,
        {k: legacy["archive"][k] for k in (*launcher.archive.fmt.DEFAULTS, "commandBytes")}, lambda: None, version=3)
    run = runType(writer, definitionValue, composer, legacy)
    run.observationPolicy = launcher.observations.archivePolicy(legacy["observation"], "not-requested")
    try:
        run.initial(suite, launcher.provenance(legacy))
        suite.update(status="failed", endedTimeNs=time.time_ns(), durationNs=0)
        run.suiteOutcome(suite)
        run.seal("failed")
    finally:
        writer.close()
    before = {p.name: p.read_bytes() for p in (writer.path / "artifacts").iterdir()}
    journal = (writer.path / "records.log").read_bytes()
    monkeypatch.setattr(pf, "sys", SimpleNamespace(platform="linux"))
    view = reader.inspectArchive(writer.path, includeContent=True)
    assert view["status"] == "sealed" and view["lastObservedSuiteStatus"] == "failed"
    assert view["recipeProvenance"] == {"ty-check": "historical-isolated-unverified"}
    policies = [v["value"] for v in view["content"] if type(v["value"]) is dict
        and v["value"].get("schemaId") == pf.PREFIX + "tool-policy@1"]
    assert policies[0]["commands"]["ty-check"]["arguments"] == OLD
    reporting = importlib.import_module("first-party.applications.experimentRunner.reporting")
    reporting.reportSchema.validate(reporting.report(writer.path))
    assert before == {p.name: p.read_bytes() for p in (writer.path / "artifacts").iterdir()}
    assert journal == (writer.path / "records.log").read_bytes()
    assert not rig[0]["processes"] and rig[0]["calls"] == 0
