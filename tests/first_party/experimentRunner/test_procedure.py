# file: tests/first_party/experimentRunner/test_procedure.py ; version: 1
"""EX-06 actual runtime with explicitly synthetic model transport and configured CLI IO."""
import copy
import importlib
import json
from pathlib import Path
import sys
import time
from dataclasses import replace

import pytest

from backend.process.context import ProcessFacade
from backend.process.capture import CapturedStream, ProcessCleanup, ProcessOutcome
from backend.process.capture import ProcessOperationError, ProcessCancelled
from tests.first_party.experimentRunner.test_experimentRunner import rig, stream, definition as legacy
from tests.first_party.experimentRunner.test_archive import freshChild

launcher = importlib.import_module("first-party.applications.experimentRunner.launcher")
pf = launcher.procedureFormat
inputs = launcher.procedureInputs
reader = importlib.import_module("first-party.applications.experimentRunner.archiveReader")


def definition():
    """Return the bounded nine-step one-pass recipe with explicit feedback."""
    old = legacy()
    return dict(schemaId=pf.PREFIX + "definition@3", experimentId="procedure-demo", mode="classic",
        configurations=old["configurations"], repetitions=1, errorPolicy="stop", contextPolicy="fresh-best-effort",
        inputs={}, procedure=[
            dict(id="request", op="message", role="user", parts=[dict(literal="Write Python.")]),
            dict(id="draft", op="inference"),
            dict(id="save", op="save", inference="draft", extract="one-python-fence", artifact="source", path="src/main.py"),
            *[dict(id=name, op="cli", command=name, artifact="source") for name in pf.RECIPES],
            dict(id="feedback", op="message", role="user", parts=[dict(literal="Correct this:\n"),
                dict(artifact="source", revision="current"), *[dict(cli=name) for name in pf.RECIPES]]),
            dict(id="final", op="inference")])


def host5(host):
    """Use test Python only as an observed executable; no Ruff/ty is launched."""
    value = copy.deepcopy(host)
    value.update(schemaId=pf.PREFIX + "host@5", observation=launcher.observations.defaults(),
        processTools=dict(ruff=sys.executable, ty=sys.executable), procedureLimits=dict(pf.MAXIMA))
    value["archive"].update(commandBytes=64 * 1024 * 1024, partialRecords=2, requestBytes=8 * 1024 * 1024)
    value["procedureLimits"].update(artifactBytes=1024, streamBytes=1024, messageBytes=32768)
    value["approvedCommands"] = {name: dict(tool=t, arguments=args, allowedExitCodes=exits, mutates=mutates,
        timeoutSeconds=2, stdoutBytes=32, stderrBytes=32, cleanupSeconds=1)
        for name, (t, args, exits, mutates) in pf.RECIPES.items()}
    return value


def syntheticCli(monkeypatch, state):
    """Replace only process OS boundary, preserving actual prepare and archive ACK."""
    calls = []

    def execute(self, ticket, *, beforeLaunch):
        """ACK exact actual-runtime request, then simulate the fixed tool's sole effect."""
        assert beforeLaunch(ticket.request) is True
        ticket._used = True
        request = ticket.request
        calls.append(request.snapshot())
        file = Path(request.arguments[-1])
        if "--fix" in request.arguments:
            file.write_bytes(b"x = 1\n")
        now = time.time_ns()
        return ProcessOutcome(request, "completed", "completed", "started", now, now, 0,
            1 if "check" in request.arguments else 0, None,
            CapturedStream(b"diagnostic", 10, True, False, False), CapturedStream(b"", 0, True, False, False),
            ProcessCleanup("owned-child-ack-spawn-supervisor-and-capture-workers", True, True, True,
                True, True, True, False, "confirmed", None, "released-pin"), None)

    monkeypatch.setattr(ProcessFacade, "execute", execute)
    state["streams"] = [stream("Original draft\n```python\nx=1\n```\n"), stream("Corrected final")] * 4
    return calls


def test_nine_step_runtime_archive(rig, monkeypatch):
    """Keep original history and independent unit workspaces with exact CLI receipts."""
    state, host, destination = rig
    calls = syntheticCli(monkeypatch, state)
    value = definition()
    value["configurations"] *= 2
    value["configurations"][1] = copy.deepcopy(value["configurations"][1])
    value["configurations"][1]["name"] = "second"
    value["repetitions"] = 2
    snapshot, path = launcher.execute(launcher.prepare(value, host5(host)), destination, handleInterrupt=False)
    data = snapshot.value()
    assert launcher.exitCode(data) == 0, data
    assert path.exists()
    assert state["calls"] == 8 and len(calls) == 16
    assert len({r["workingDirectory"] for r in calls}) == 4
    assert len({r["invocationId"] for r in calls}) == 4
    for unit in data["suite"]["units"]:
        assert len(unit["run"]["steps"]) == 9
        final = unit["run"]["turns"][1]["query"]["messages"]
        assert len(final) == 3 and "x=1" in final[1]["content"]
        assert "x = 1" in final[2]["content"] and "diagnostic" in final[2]["content"]
        assert unit["run"]["workspaceDisposition"] == "ephemeral-ended"
    recovered = reader.inspectArchive(data["archive"]["path"])
    assert recovered["status"] == "sealed", recovered
    reporting = importlib.import_module("first-party.applications.experimentRunner.reporting")
    exporting = importlib.import_module("first-party.applications.experimentRunner.exporting")
    report = reporting.report(data["archive"]["path"])
    reporting.reportSchema.validate(report)
    assert len(report["rows"]) == 8 and len(report["procedureSteps"]) == 36
    assert len(report["cliMetrics"]) == 16 and "privateContent" not in report
    assert report["metrics"][0]["contributing"] == 8
    assert "Original draft" not in exporting.table(report) and "src/main.py" not in exporting.table(report)
    assert b"step-summary-csv@1" in exporting.csvBytes(report)
    unit = data["suite"]["units"][0]["unitId"]
    compared = reporting.compare(data["archive"]["path"], unit, "final", data["archive"]["path"], unit, "final", mode=reporting.MODES[0])
    reporting.reportSchema.validate(compared)
    assert compared["result"] == "Equal" and compared["schemaId"].endswith("comparison@2")
    assert b"procedureQualification" in exporting.csvBytes(compared)
    marker = exporting.publish(report, destination, "procedure-report", kind="report", format="csv", sourceDirectories=[data["archive"]["path"]])
    assert marker["publicationState"] == "complete"
    assert marker["data"]["sha256"] == launcher.archive.fmt.digest((destination / "procedure-report" / "report.csv").read_bytes())
    private = reporting.report(data["archive"]["path"], unitIds=[unit], turnIds=["final"], includeContent=True)
    reporting.reportSchema.validate(private)
    assert private["privateContent"]


@pytest.mark.parametrize("raw", [b'{a:1,a:2}', b'{a:NaN}', b'{a:Infinity}', b'{} {}', b'[' * 33 + b']' * 33],
    ids=["duplicate", "nan", "infinity", "trailing", "depth"])
def test_reject_json5(raw):
    """Reject malformed/oversized authoring before any runtime or tool operation."""
    with pytest.raises(ValueError):
        inputs.parse(raw, "json5")


@pytest.mark.parametrize("fault", ["later-config", "forward-input", "forward-artifact", "forward-cli", "role", "duplicate",
    "traversal", "device", "case-collision", "extra-step", "override", "message-budget", "argv-budget", "nodes", "depth",
    "queue-budget", "archive-budget", "record-budget"])
def test_all_configuration_preflight(rig, fault):
    """Reject invalid expansion before adapter construction or active IO."""
    value, host = definition(), host5(rig[1])
    if fault == "later-config":
        value["configurations"].append(copy.deepcopy(value["configurations"][0]))
        value["configurations"][-1].update(name="late")
        value["configurations"][-1]["defaults"]["maxTokens"] = 4096
    elif fault.startswith("forward-"):
        key = fault.removeprefix("forward-")
        value["procedure"][0]["parts"] = [dict(artifact="source", revision="current") if key == "artifact" else {key: "later"}]
    elif fault == "role":
        value["procedure"][0]["role"] = "system"
    elif fault == "duplicate":
        value["procedure"][-1]["id"] = "draft"
    elif fault in ("traversal", "device"):
        value["procedure"][2]["path"] = "../main.py" if fault == "traversal" else "NUL.py"
    elif fault == "case-collision":
        value["procedure"].insert(3, dict(id="again", op="save", inference="draft", extract="whole-answer", artifact="other", path="SRC/Main.py"))
    elif fault == "extra-step":
        value["procedure"][0]["shell"] = "unapproved"
    elif fault == "override":
        value["procedure"][1]["overrides"] = dict(topP=.5)
    elif fault == "message-budget":
        host["procedureLimits"]["messageBytes"] = 4
    elif fault == "argv-budget":
        host["procedureLimits"]["argvBytes"] = 4
    elif fault in ("nodes", "depth"):
        host["procedureLimits"]["definitionNodes" if fault == "nodes" else "definitionDepth"] = 4
    elif fault == "queue-budget":
        host["archive"]["commandBytes"] = 65536
    elif fault == "archive-budget":
        host["archive"]["archiveBytes"] = 1048576
    else:
        host["archive"]["logicalRecords"] = 4
    with pytest.raises(ValueError):
        launcher.prepare(value, host)
    assert rig[0]["calls"] == 0 and not rig[0]["processes"]


@pytest.mark.parametrize("fault", ["prepare", "timeout", "overflow", "cancel", "unknown", "missing", "invalid-utf8", "read-only-change", "exit"])
def test_process_failure_receipts_and_no_retry(rig, monkeypatch, fault):
    """A failed procedure retains actual process facts and stops before another inference/unit."""
    state, host, destination = rig
    calls = syntheticCli(monkeypatch, state)
    synthetic = ProcessFacade.execute

    def execute(self, ticket, *, beforeLaunch):
        """Inject one explicit failure after the actual exact-request ACK."""
        observed = synthetic(self, ticket, beforeLaunch=beforeLaunch)
        file = Path(ticket.request.arguments[-1])
        if fault in ("timeout", "cancel", "overflow", "unknown"):
            status = {"timeout": "timeout", "cancel": "cancelled", "overflow": "output-limit", "unknown": "failed"}[fault]
            reason = {"timeout": "timeout", "cancel": "cancelled", "overflow": "stdout-limit", "unknown": None}[fault]
            changes = dict(status=status, phase="capture", stopReason=reason)
            if fault == "overflow":
                changes["stdout"] = CapturedStream(b"x" * 32, 33, False, True, False)
            if fault == "unknown":
                changes.update(phase="cleanup", failureCategory="cleanup-unknown", cleanup=replace(observed.cleanup,
                    workersSettled=False, disposition="unknown", retentionId=ticket.request.requestId, workspaceDisposition="retained"))
            observed = replace(observed, **changes)
            raise ProcessCancelled(observed) if fault == "cancel" else ProcessOperationError(status, observed)
        if fault == "missing":
            file.unlink()
        elif fault == "invalid-utf8":
            file.write_bytes(b"\xff")
        elif fault == "exit":
            observed = replace(observed, exitCode=42)
        elif fault == "read-only-change" and len(calls) == 3:
            file.write_bytes(b"changed = True\n")
        return observed

    if fault == "prepare":
        def prepare(*args, **kwargs):
            """Fail before preparing a request, with no process outcome invented."""
            raise RuntimeError("private prepare sentinel")
        monkeypatch.setattr(ProcessFacade, "prepare", prepare)
    else:
        monkeypatch.setattr(ProcessFacade, "execute", execute)
    value = definition()
    value["repetitions"] = 2
    snapshot, _ = launcher.execute(launcher.prepare(value, host5(host)), destination, handleInterrupt=False)
    result = snapshot.value()
    assert launcher.exitCode(result) == 1 and state["calls"] == 1
    assert len(calls) == (0 if fault == "prepare" else 3 if fault == "read-only-change" else 1)
    assert result["suite"]["units"][1]["status"] == "not-started"
    view = reader.inspectArchive(result["archive"]["path"])
    assert view["status"] == "sealed", view
    steps = view["procedureSteps"]
    terminal = next(s for s in steps if s["status"] in ("failed", "cancelled"))
    assert terminal["op"] == "cli"
    if fault == "unknown":
        assert result["suite"]["units"][0]["run"]["workspaceDisposition"] == "retained"
    reporting = importlib.import_module("first-party.applications.experimentRunner.reporting")
    report = reporting.report(result["archive"]["path"])
    reporting.reportSchema.validate(report)
    metric = next(m for m in report["cliMetrics"] if m["opId"] == terminal["opId"] and m["unitId"] == terminal["unitId"])
    assert metric["availability"] == ("unavailable" if fault == "prepare" else "available")


def test_frozen_json5_inputs_and_offline(rig, monkeypatch, tmp_path):
    """Freeze once, survive original deletion, and inspect/export without runtime/parser imports."""
    state, host, destination = rig
    syntheticCli(monkeypatch, state)
    value = definition()
    value["inputs"] = dict(prompt="prompt.txt")
    value["procedure"][0]["parts"].append(dict(input="prompt"))
    sourcePath, prompt = tmp_path / "procedure.json5", tmp_path / "prompt.txt"
    prompt.write_bytes("Frozen café\n".encode())
    raw = ("// authored comment\n" + json.dumps(value, ensure_ascii=False)).encode()
    sourcePath.write_bytes(raw)
    prepared = launcher.prepare(inputs.readDefinition(sourcePath, "json5"), host5(host))
    prompt.unlink()
    sourcePath.unlink()
    snapshot, _ = launcher.execute(prepared, destination, handleInterrupt=False)
    result = snapshot.value()
    assert launcher.exitCode(result) == 0, result
    assert "Frozen café" in result["suite"]["units"][0]["run"]["turns"][0]["input"]
    unit = result["suite"]["units"][0]["unitId"]
    code = r'''
import sys, importlib, json
sys.dont_write_bytecode = True
r = importlib.import_module('first-party.applications.experimentRunner.reporting')
e = importlib.import_module('first-party.applications.experimentRunner.exporting')
for name in ('definition', 'procedureFormat', 'procedureGrammar', 'procedureReporting', 'observationFormat'):
    importlib.import_module('first-party.applications.experimentRunner.' + name)
def audit(event, args):
    """Deny active IO and writes after stdlib-only code bootstrap."""
    if event.startswith(('socket.', 'subprocess.')):
        raise AssertionError('active IO')
    if event == 'open':
        mode, flags = args[1:3]
        if isinstance(mode, str) and any(c in mode for c in 'wax+') or isinstance(flags, int) and flags & 3:
            raise AssertionError('write')
        if isinstance(args[0], str) and not args[0].startswith(sys.argv[1]):
            raise AssertionError('external source/tool read')
sys.addaudithook(audit)
path, unit = sys.argv[1:]
report = r.report(path)
r.reportSchema.validate(report)
e.table(report)
e.csvBytes(report)
assert r.compare(path, unit, 'final', path, unit, 'final', mode=r.MODES[2])['result'] == 'Equal'
assert not any(n == 'backend' or n.startswith('backend.') or n == 'json5' or n.startswith('json5.') for n in sys.modules)
print(e.jsonBytes(report).decode())
'''
    child = freshChild(monkeypatch, code, result["archive"]["path"], unit)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)["schemaId"].endswith("report@2")


@pytest.mark.parametrize("answer", ["no fence", "```Python\nx=1\n```", "```python\n```", "```python\nx=1\n```\n```python\ny=2\n```"])
def test_strict_fence(answer):
    """Ambiguous, empty or mismatched fences cannot become executable artifacts."""
    with pytest.raises(ValueError):
        pf.extract(answer, "one-python-fence", 1024)


def test_semantic_input_hash_excludes_observation_and_location():
    """Content identity is stable across times/paths and changes with logical IDs/bytes."""
    ref, _ = launcher.archive.artifact(b"hello")
    entry = dict(inputId="a", rawRef=ref, sourceRelativePath="first", observation=dict(time=1))
    expected = pf.semanticInputs([entry])
    entry.update(sourceRelativePath="second", observation=dict(time=999))
    assert pf.semanticInputs([entry]) == expected
    entry["inputId"] = "b"
    assert pf.semanticInputs([entry]) != expected


def test_save_failure_retains_extracted_bytes(rig, monkeypatch):
    """A failed materialization keeps original answer/extraction without inventing a revision."""
    from backend.workspace.context import WorkspaceFacade
    syntheticCli(monkeypatch, rig[0])
    def fail(*args, **kwargs):
        """Fail only the mediated write, after extraction has completed."""
        raise OSError("private write failure")
    monkeypatch.setattr(WorkspaceFacade, "materializeBytes", fail)
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    value = snapshot.value()
    view = reader.inspectArchive(value["archive"]["path"], includeContent=True)
    assert view["status"] == "sealed" and value["suite"]["status"] == "failed", value
    docs = [item["value"] for item in view["content"] if type(item["value"]) is dict]
    saved = next(d for d in docs if d.get("schemaId") == pf.PREFIX + "save-output@1")
    assert saved["extractedRef"]["bytes"] == len(b"x=1\n") and saved["revisionRef"] is None


def test_ack_visible_but_not_returned_prevents_cli(rig, monkeypatch):
    """A committed request is no ACK-return/spawn receipt; poison retains the pending prefix."""
    calls = syntheticCli(monkeypatch, rig[0])
    original = launcher.archive.ArchiveWriter._commit
    def fail(writer, command, journal):
        """Fail immediately after actual durable admission publication, before caller ACK."""
        original(writer, command, journal)
        if command["kind"] == "process-admitted":
            raise OSError("private ACK return failure")
    monkeypatch.setattr(launcher.archive.ArchiveWriter, "_commit", fail)
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    value = snapshot.value()
    assert not calls and rig[0]["calls"] == 1 and value["archive"]["failure"] is not None
    view = reader.inspectArchive(value["archive"]["path"])
    assert view["status"] == "incomplete"
    assert view["records"][-1]["kind"] == "process-admitted"
    assert next(s for s in view["procedureSteps"] if s["op"] == "cli")["status"] == "pending"


def rewrite3(path, records):
    """Rehash a synthetic semantic corruption without hiding it behind broken framing."""
    fmt = launcher.archive.fmt
    previous, lines = fmt.digest((path / "manifest.json").read_bytes()), []
    for sequence, record in enumerate(records, 1):
        record.update(sequence=sequence, previousCommitHash=previous)
        record["dataSha256"] = fmt.hashWithout(record, "dataSha256")
        commit = dict(schemaId=fmt.PREFIX + "archive-commit@3", sequence=sequence,
            dataSha256=record["dataSha256"], previousCommitHash=previous)
        commit["commitSha256"] = fmt.hashWithout(commit, "commitSha256")
        lines.extend((fmt.canonical(record) + b"\n", fmt.canonical(commit) + b"\n"))
        previous = commit["commitSha256"]
    (path / "records.log").write_bytes(b"".join(lines))


@pytest.mark.parametrize("fault", ["op-order", "missing-admission", "step-success", "revision-hash", "unknown-completed", "bad-stream-fact"])
def test_rehashed_semantic_corruption_preserves_prefix(rig, monkeypatch, fault):
    """Full and filtered readers reject the same contradictory typed evidence record."""
    syntheticCli(monkeypatch, rig[0])
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    value, fmt = snapshot.value(), launcher.archive.fmt
    path = Path(value["archive"]["path"])
    records = [json.loads(line) for line in (path / "records.log").read_bytes().splitlines()[::2]]
    if fault == "op-order":
        target = next(r for r in records if r["kind"] == "step-planned")
        target["payload"]["ordinal"] = 2
    elif fault == "missing-admission":
        records = [r for r in records if r["kind"] != "process-admitted"]
    else:
        schemaId = {"step-success": "step-outcome@1", "revision-hash": "file-revision@1",
            "unknown-completed": "process-outcome@1", "bad-stream-fact": "process-outcome@1"}[fault]
        found = False
        for target in records:
            for i, ref in enumerate(target["artifacts"]):
                if ref["mediaType"] != "application/json":
                    continue
                doc = json.loads((path / "artifacts" / (ref["sha256"] + ".blob")).read_bytes())
                if type(doc) is dict and doc.get("schemaId") == pf.PREFIX + schemaId:
                    if fault == "step-success":
                        doc["outputRef"] = None
                    elif fault == "revision-hash":
                        doc["workspaceObservation"]["sha256"] = "0" * 64
                    elif fault == "unknown-completed":
                        doc["cleanup"].update(disposition="unknown", workersSettled=False)
                    else:
                        doc["stdoutFacts"]["observedBytes"] = 0
                    raw = fmt.canonical(doc)
                    changed = dict(ref, bytes=len(raw), sha256=fmt.digest(raw))
                    (path / "artifacts" / (changed["sha256"] + ".blob")).write_bytes(raw)
                    target["artifacts"][i] = changed
                    def substitute(item):
                        """Change direct payload references only, keeping the record hash internally valid."""
                        if type(item) is dict:
                            return changed if item == ref else {k: substitute(v) for k, v in item.items()}
                        return [substitute(v) for v in item] if type(item) is list else item
                    target["payload"] = substitute(target["payload"])
                    found = True
                    break
            if found:
                break
        assert found
    rewrite3(path, records)
    view = reader.inspectArchive(path)
    selected = reader.inspectArchive(path, unitId=value["suite"]["units"][0]["unitId"], turnId="draft")
    assert view["status"] == selected["status"] == "corrupt"
    assert view["committedSequence"] == selected["committedSequence"]


def test_valid_crash_prefixes_are_pending(rig, monkeypatch):
    """Truncated committed prefixes keep observed intent/output distinct with no invented completion."""
    syntheticCli(monkeypatch, rig[0])
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    path = Path(snapshot.value()["archive"]["path"])
    journal = path / "records.log"
    raw = journal.read_bytes()
    lines = raw.splitlines(keepends=True)
    for kind in ("run-inputs", "step-planned", "process-admitted", "step-completed"):
        index = next(i for i in range(0, len(lines), 2) if json.loads(lines[i])["kind"] == kind)
        journal.write_bytes(b"".join(lines[:index + 2]))
        view = reader.inspectArchive(path)
        assert view["status"] == "incomplete" and view["committedSequence"] == index // 2 + 1
    journal.write_bytes(b"".join(lines[:index + 1]))
    view = reader.inspectArchive(path)
    assert view["status"] == "incomplete" and view["tail"] == "uncommitted-data"
    journal.write_bytes(raw)


def test_procedure_observation_selection_and_cli_gap(rig, monkeypatch):
    """Select inference only and preserve final-copy guards through an explicit CLI gap."""
    state, host, destination = rig
    syntheticCli(monkeypatch, state)
    host = host5(host)
    selected = dict(configurationIndex=1, repetition=1, turnId="draft")
    host["observation"].update(enabled=True, calls=[selected])
    held = []
    original = launcher.observations.Controller.begin
    def begin(controller, ctx, port, turnId):
        """Retain observation-only handles for deterministic lifecycle assertions."""
        call = original(controller, ctx, port, turnId)
        if call is not None:
            held.append(call)
        return call
    monkeypatch.setattr(launcher.observations.Controller, "begin", begin)
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host), destination, handleInterrupt=False)
    value = snapshot.value()
    assert launcher.exitCode(value) == 0, value
    view = reader.inspectArchive(value["archive"]["path"])
    assert view["status"] == "sealed" and len(view["observations"]) == 1
    assert len(held) == 1 and not held[0].active and held[0].sessionRelease == "completed"
    host["observation"]["calls"][0]["turnId"] = "ruff-check"
    with pytest.raises((ValueError, launcher.archive.fmt.ArchiveError)):
        launcher.prepare(definition(), host)


@pytest.mark.parametrize("capture", ["required", "disabled"])
def test_authored_assistant_history_and_capture_policy(rig, monkeypatch, capture):
    """Explicit authored assistant origin and consecutive user messages remain exact history."""
    syntheticCli(monkeypatch, rig[0])
    host = host5(rig[1])
    host["archive"]["capturePolicy"] = capture
    value = definition()
    value["procedure"].insert(0, dict(id="priming", op="message", role="assistant", parts=[dict(literal="Authored priming")]))
    value["procedure"].insert(2, dict(id="more", op="message", role="user", parts=[dict(literal="Additional user message")]))
    snapshot, _ = launcher.execute(launcher.prepare(value, host), rig[2], handleInterrupt=False)
    result = snapshot.value()
    assert launcher.exitCode(result) == 0, result
    messages = result["suite"]["units"][0]["run"]["turns"][0]["query"]["messages"]
    assert [m["role"] for m in messages] == ["assistant", "user", "user"]
    assert messages[0]["content"] == "Authored priming"
    assert reader.inspectArchive(result["archive"]["path"])["status"] == "sealed"


def test_first_critical_process_exit_survives_session_close(rig, monkeypatch):
    """Preserve a same-object critical process primary through an independently failing close."""
    from backend.llm.executionSessions import LlmConversationSession
    class Critical(BaseException):
        """A false-valued primary detects accidental truthiness-based replacement."""
        def __bool__(self):
            """Remain a valid first primary even though boolean conversion is false."""
            return False
    primary, secondary = Critical(), SystemExit(7)
    syntheticCli(monkeypatch, rig[0])
    synthetic, close = ProcessFacade.execute, LlmConversationSession.close
    def execute(self, ticket, *, beforeLaunch):
        """Attach immutable shared evidence to the original critical object."""
        primary.processEvidence = synthetic(self, ticket, beforeLaunch=beforeLaunch)
        raise primary
    def failedClose(self):
        """Queue real close before a distinct critical reporting failure."""
        close(self)
        raise secondary
    monkeypatch.setattr(ProcessFacade, "execute", execute)
    monkeypatch.setattr(LlmConversationSession, "close", failedClose)
    with pytest.raises(Critical) as caught:
        launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    assert caught.value is primary and rig[0]["calls"] == 1
    assert primary.experimentExecution.value()["suite"]["status"] == "failed"


def test_same_frozen_bytes_compare_across_new_inode_and_changed_input(rig, monkeypatch, tmp_path):
    """Default qualifications use semantic inputs, with explicit disclosure for changed inputs."""
    syntheticCli(monkeypatch, rig[0])
    reporting = importlib.import_module("first-party.applications.experimentRunner.reporting")
    value, prompt = definition(), tmp_path / "identity.txt"
    value["inputs"] = dict(prompt="identity.txt")
    value["procedure"][0]["parts"].append(dict(input="prompt"))
    outcomes = []
    for content in (b"same bytes", b"same bytes", b"changed bytes"):
        if prompt.exists():
            prompt.unlink()
        prompt.write_bytes(content)
        source = inputs.Source(json.dumps(value).encode(), launcher.schema.canonical(value), tmp_path, "json")
        snapshot, _ = launcher.execute(launcher.prepare(source, host5(rig[1])), rig[2], handleInterrupt=False)
        data = snapshot.value()
        assert launcher.exitCode(data) == 0, data
        outcomes.append((data["archive"]["path"], data["suite"]["units"][0]["unitId"], "draft"))
    same = reporting.compare(*outcomes[0], *outcomes[1], mode=reporting.MODES[0])
    assert same["result"] == "Equal"
    q = same["procedureQualification"]
    assert q["leftInputsSha256"] == q["rightInputsSha256"]
    assert same["alignment"]["provenanceSha256"][0] != same["alignment"]["provenanceSha256"][1]
    different = reporting.compare(*outcomes[0], *outcomes[2], mode=reporting.MODES[0])
    assert different["result"] == "NotComparable"
    allowed = reporting.compare(*outcomes[0], *outcomes[2], mode=reporting.MODES[0], allowInputDifferences=True)
    assert allowed["result"] == "Equal" and allowed["procedureQualification"]["differencesAllowed"]
    assert allowed["procedureQualification"]["leftInputsSha256"] != allowed["procedureQualification"]["rightInputsSha256"]


@pytest.mark.parametrize("content", [b"\xff", b"\xef\xbb\xbfbom", b"nul\x00", b"x" * 262145], ids=["utf8", "bom", "nul", "over-limit"])
def test_invalid_frozen_input_before_io(rig, tmp_path, content):
    """Strict UTF-8 and read limits reject originals before model/runtime/process work."""
    value = definition()
    value["inputs"] = dict(prompt="bad.txt")
    (tmp_path / "bad.txt").write_bytes(content)
    source = inputs.Source(json.dumps(value).encode(), launcher.schema.canonical(value), tmp_path, "json")
    with pytest.raises(ValueError):
        launcher.prepare(source, host5(rig[1]))
    assert not rig[0]["processes"] and rig[0]["calls"] == 0


def test_json5_numeric_bounds_and_hex():
    """Support bounded Classic JSON5 numbers and reject long tokens before numeric conversion."""
    prefix = b'{schemaId:"actant.experiment.definition@3",n:'
    assert inputs.parse(prefix + b'0x10}', "json5")["n"] == 16
    for token in (b"1" * 129, b"0." + b"0" * 128 + b"1", b"1e999"):
        with pytest.raises(ValueError):
            inputs.parse(prefix + token + b"}", "json5")


@pytest.mark.parametrize("mode", ["data", "commit", "ack"])
def test_owned_child_crash_recovery_archive3(rig, monkeypatch, tmp_path, mode):
    """Kill only a model-free owned writer child at precise new-schema journal boundaries."""
    code = r'''
import sys, os, importlib, pathlib
from tests.first_party.experimentRunner.test_experimentRunner import hostValue
from tests.first_party.experimentRunner.test_procedure import definition, host5
l = importlib.import_module('first-party.applications.experimentRunner.launcher')
p = importlib.import_module('first-party.applications.experimentRunner.procedureArchive')
engine = importlib.import_module('first-party.applications.experimentRunner.procedure')
root, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
prepared = l.prepare(definition(), host5(hostValue(root)))
d, h, composer = prepared
composer.requestEvidence = None
suite = l.results.initialSuite(d, l.schema.unitDefinition)
writer = l.archive.ArchiveWriter(root / 'crash', suite, {k:h.value()['archive'][k] for k in (*l.archive.fmt.DEFAULTS, 'commandBytes')}, lambda:None, version=3)
run = p.Run(writer, d, composer, h.value())
run.observationPolicy = l.observations.archivePolicy(h.value()['observation'], 'not-requested')
run.initial(suite, l.provenance(h.value()))
port = run.unit(suite['units'][0], 'model-free-child-job')
original, pending = l.archive.writeAll, [False]
def write(target, raw):
    """Exit only this child after the selected fully flushed frame."""
    original(target, raw)
    if b'"kind":"step-planned"' in raw:
        pending[0] = True
        if mode == 'data':
            os._exit(17)
    elif pending[0] and b'archive-commit@3' in raw and mode == 'commit':
        os._exit(17)
l.archive.writeAll = write
step = d.value()['procedure'][0]
intent, refs, content = engine.message(port, step, {}, {})
port.planned(step, intent, refs)
os._exit(17)
'''
    child = freshChild(monkeypatch, code, tmp_path, mode)
    assert child.returncode == 17, child.stderr
    archive = next((tmp_path / "crash").glob("*/archive"))
    view = reader.inspectArchive(archive)
    assert view["status"] == "incomplete"
    assert view["procedureSteps"][0]["status"] == ("not-started" if mode == "data" else "pending")
    assert view["tail"] == ("uncommitted-data" if mode == "data" else "none")


@pytest.mark.parametrize("expiry", ["in-flight", "cli-gap"])
def test_live_observation_guard_and_original_deadline_through_cli(rig, monkeypatch, expiry):
    """Viewer expiry never destroys in-flight capture and only releases the idle original source."""
    state, host, destination = rig
    syntheticCli(monkeypatch, state)
    host = host5(host)
    selected = dict(configurationIndex=1, repetition=1, turnId="draft")
    host["observation"].update(enabled=True, calls=[selected])
    host["observation"]["live"].update(enabled=True, call=selected, allowedOrigin="http://127.0.0.1:8765", maxLifetimeSeconds=30)
    held, guarded = [], []
    originalBegin, originalExecute = launcher.observations.Controller.begin, ProcessFacade.execute
    originalCommit = launcher.archive.ArchiveWriter._commit
    def begin(controller, ctx, port, turnId):
        """Observe actual lifecycle state without capturing a runtime Context in workers."""
        call = originalBegin(controller, ctx, port, turnId)
        if call is not None:
            held.append(call)
        return call
    def onCall(index):
        """Expire viewer while the selected real source is attached to synthetic transport."""
        if index == 0 and expiry == "in-flight":
            held[0].expire()
            assert held[0].active and held[0].capture.snapshot().sessionId
            assert held[0].viewer.revoked and held[0].sessionRelease == "pending"
    def execute(self, ticket, *, beforeLaunch):
        """Release the idle original source during a CLI gap without opening another session."""
        if expiry == "cli-gap" and not held[0].expired:
            assert not held[0].active and held[0].sessionRelease == "pending"
            held[0].expire()
            assert held[0].sessionRelease == "completed"
        return originalExecute(self, ticket, beforeLaunch=beforeLaunch)
    def commit(writer, command, journal):
        """Assert the source guard remains held through the immutable call-final ACK."""
        if command["kind"] == "observation-snapshot":
            payload = launcher.archive.fmt.decode(command["payload"])
            ref = payload["observationRef"]
            doc = next(raw for item, raw in command["artifacts"] if item == ref)
            if launcher.archive.fmt.decode(doc)["phase"] == "call-final":
                guarded.append(held[0].active)
        return originalCommit(writer, command, journal)
    def handoff(notice):
        """Consume the transient private connection notice without persisting credentials."""
        assert notice.value()["turnId"] == "draft"
    monkeypatch.setattr(launcher.observations.Controller, "begin", begin)
    monkeypatch.setattr(ProcessFacade, "execute", execute)
    monkeypatch.setattr(launcher.archive.ArchiveWriter, "_commit", commit)
    state["onCall"] = onCall
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host), destination, handleInterrupt=False, observationHandoff=handoff)
    value = snapshot.value()
    assert launcher.exitCode(value) == 0, value
    assert state["calls"] == 2 and len(held) == 1 and guarded == [True]
    assert held[0].sessionRelease == "completed" and held[0].viewer.revoked
    assert reader.inspectArchive(value["archive"]["path"])["status"] == "sealed"
