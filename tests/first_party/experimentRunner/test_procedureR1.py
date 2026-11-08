# file: tests/first_party/experimentRunner/test_procedureR1.py ; version: 1
"""Unchanged independent EX-06 R1 reproducer and bounded diagnostic failure coverage."""
from pathlib import Path

# Keep the reviewed raw bytes separate; collect their original tests unchanged.
exec(compile(Path(__file__).with_name("ex06_app_r1_reproducer.txt").read_bytes(),
             str(Path(__file__).with_name("ex06_app_r1_reproducer.txt")), "exec"))

from backend.context.codeEntryContext import _LlmFacade
from backend.process.capture import ProcessCancelled

portType = importlib.import_module("first-party.applications.experimentRunner.procedureArchive").Port
entry = importlib.import_module("first-party.applications.experimentRunner.codeEntry")
engine = importlib.import_module("first-party.applications.experimentRunner.procedure")
reader = importlib.import_module("first-party.applications.experimentRunner.archiveReader")
SECRET = "PRIVATE_DIAGNOSTIC_SECONDARY_R1"


class FalsePrimary(BaseException):
    """Detect truthiness-based replacement of a valid first critical exception."""

    def __bool__(self):
        """Return false while preserving an independently meaningful exception identity."""
        return False


@pytest.mark.parametrize("secondaryKind", [ValueError, SystemExit], ids=["ordinary", "critical"])
@pytest.mark.parametrize("stage", ["stream-ref", "process-outcome", "post-revision", "cli-output", "cli-terminal",
    "cli-terminal-worker", "inference-output", "inference-terminal", "inference-terminal-worker",
    "inference-final-publish", "procedure-final-publish"])
def test_false_primary_survives_all_required_diagnostics(rig, monkeypatch, stage, secondaryKind):
    """Keep exact first exit, frozen process facts and pending evidence without retries or leaks."""
    first, second = FalsePrimary(), secondaryKind(SECRET)
    syntheticCli(monkeypatch, rig[0])
    actualProcess, actualInference = ProcessFacade.execute, _LlmFacade.run
    put, doc, terminal, publish = portType.put, portType.doc, portType.terminal, entry.Recorder.publish
    commit = launcher.archive.ArchiveWriter._commit
    raised, failures, writers = [], [], []
    inferenceStage = stage.startswith("inference-")

    def process(self, ticket, *, beforeLaunch):
        """Run the synthetic tool once before raising the exact false-valued primary."""
        first.processEvidence = actualProcess(self, ticket, beforeLaunch=beforeLaunch)
        first.evidence = None
        raised.append(first)
        raise first

    def inference(self, **kwargs):
        """Preserve actual call cleanup and exact wire facts before injecting interruption."""
        response = actualInference(self, **kwargs)
        first.llmCleanup = response.executionEvidence
        raised.append(first)
        raise first

    def failedPut(self, raw, **kwargs):
        """Fail only a post-process raw stream reference allocation."""
        if raised and stage == "stream-ref" and raw == b"diagnostic":
            failures.append(second)
            writers.append(self.writer)
            raise second
        return put(self, raw, **kwargs)

    def failedDoc(self, name, **fields):
        """Fail required output/revision materialization only after the earlier primary."""
        target = {"process-outcome": "process-outcome@1", "post-revision": "file-revision@1",
            "cli-output": "cli-output@1", "inference-output": "inference-output@1"}.get(stage)
        if raised and name == target:
            failures.append(second)
            writers.append(self.writer)
            raise second
        return doc(self, name, **fields)

    def failedTerminal(self, step, value, refs):
        """Fail the actual mandatory terminal boundary without inventing a successful ACK."""
        if raised and stage == step["op"] + "-terminal":
            failures.append(second)
            writers.append(self.writer)
            raise second
        return terminal(self, step, value, refs)

    def failedPublish(self, result):
        """Fail inference-final or enclosing final immutable diagnostic publication."""
        if raised and (stage == "inference-final-publish" or stage == "procedure-final-publish" and result["endedTimeNs"] is not None):
            failures.append(second)
            writers.append(self.archivePort.writer)
            raise second
        return publish(self, result)

    def failedCommit(writer, command, journal):
        """Fail on the actual writer worker after visible COMMIT but before returned terminal ACK."""
        if raised and stage.endswith("-terminal-worker") and command["kind"] == "step-completed":
            opId = launcher.archive.fmt.decode(command["payload"])["opId"]
            if opId == ("draft" if inferenceStage else "ruff-safe-fix"):
                commit(writer, command, journal)
                failures.append(second)
                writers.append(writer)
                raise second
        return commit(writer, command, journal)

    originalExecute = engine.execute
    def execute(recorder, ctx, runId):
        """Patch the actual runtime-loaded recorder class, distinct from helper imports."""
        monkeypatch.setattr(type(recorder), "publish", failedPublish)
        return originalExecute(recorder, ctx, runId)

    monkeypatch.setattr(_LlmFacade if inferenceStage else ProcessFacade, "run" if inferenceStage else "execute",
                        inference if inferenceStage else process)
    monkeypatch.setattr(portType, "put", failedPut)
    monkeypatch.setattr(portType, "doc", failedDoc)
    monkeypatch.setattr(portType, "terminal", failedTerminal)
    monkeypatch.setattr(launcher.archive.ArchiveWriter, "_commit", failedCommit)
    monkeypatch.setattr(engine, "execute", execute)
    with pytest.raises(FalsePrimary) as caught:
        launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    assert caught.value is first and failures and len(raised) == rig[0]["calls"] == 1
    writer = writers[0]
    assert writer.failure is not None and writer.primary is first
    value = first.experimentExecution.value()
    assert SECRET not in str(value) and value["archive"]["failure"] is not None
    view = reader.inspectArchive(value["archive"]["path"])
    assert view["status"] == "incomplete"
    assert value["suite"]["units"][0]["jobOutcome"] != "Succeeded"
    if not inferenceStage:
        assert first.processEvidence.status == "completed" and first.processEvidence.exitCode == 1
        if stage != "procedure-final-publish":
            assert value["suite"]["units"][0]["run"]["processBoundary"] == "confirmed"


@pytest.mark.parametrize("stage", ["process-outcome@1", "cli-output@1", "inference-output@1"])
@pytest.mark.parametrize("kind", [ValueError, SystemExit], ids=["ordinary", "critical"])
def test_first_diagnostic_failure_still_fails(rig, monkeypatch, stage, kind):
    """With no earlier primary critical exits propagate and ordinary failures fail the Job."""
    second, failures = kind(SECRET), []
    syntheticCli(monkeypatch, rig[0])
    original = portType.doc
    def failedDoc(self, name, **fields):
        """Inject a first critical error at the explicitly selected required output stage."""
        if name == stage:
            failures.append(second)
            raise second
        return original(self, name, **fields)
    monkeypatch.setattr(portType, "doc", failedDoc)
    if kind is SystemExit:
        with pytest.raises(SystemExit) as caught:
            launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
        assert caught.value is second
        value = second.experimentExecution.value()
    else:
        snapshot, _ = launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
        value = snapshot.value()
    assert failures and rig[0]["calls"] == 1
    assert value["suite"]["units"][0]["jobOutcome"] != "Succeeded"
    assert SECRET not in str(value) and value["archive"]["failure"] is not None
    assert reader.inspectArchive(value["archive"]["path"])["status"] == "incomplete"


@pytest.mark.parametrize("stage", ["document", "terminal-worker"])
def test_cancelled_process_not_replaced_by_critical_diagnostic(rig, monkeypatch, stage):
    """An earlier cancellation keeps actual Job cancellation when archive bookkeeping fails."""
    first, second, failures = ProcessCancelled(), SystemExit(SECRET), []
    syntheticCli(monkeypatch, rig[0])
    actual, doc = ProcessFacade.execute, portType.doc
    commit = launcher.archive.ArchiveWriter._commit
    def cancelled(self, ticket, *, beforeLaunch):
        """Retain exact immutable process facts while reporting an actual cancellation exit."""
        first.evidence = actual(self, ticket, beforeLaunch=beforeLaunch)
        raise first
    def failedDoc(self, name, **fields):
        """Keep a later critical archive failure independent of the earlier cancellation."""
        if stage == "document" and name == "cli-output@1":
            failures.append(second)
            raise second
        return doc(self, name, **fields)
    def failedCommit(writer, command, journal):
        """A secondary critical on the actual worker cannot downgrade earlier cancellation."""
        if stage == "terminal-worker" and command["kind"] == "step-completed" and launcher.archive.fmt.decode(command["payload"])["opId"] == "ruff-safe-fix":
            failures.append(second)
            raise second
        return commit(writer, command, journal)
    monkeypatch.setattr(ProcessFacade, "execute", cancelled)
    monkeypatch.setattr(portType, "doc", failedDoc)
    monkeypatch.setattr(launcher.archive.ArchiveWriter, "_commit", failedCommit)
    snapshot, _ = launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    value = snapshot.value()
    assert failures and rig[0]["calls"] == 1
    assert value["suite"]["units"][0]["jobOutcome"] == "Cancelled"
    assert value["suite"]["status"] == "cancelled" and value["archive"]["failure"] is not None
    assert SECRET not in str(value)


def test_unknown_process_not_repaired_by_secondary_diagnostics(rig, monkeypatch):
    """Retain immutable UNKNOWN/workspace facts and avoid post-read after diagnostic failure."""
    from dataclasses import replace
    first, second = FalsePrimary(), SystemExit(SECRET)
    syntheticCli(monkeypatch, rig[0])
    actual, read, doc = ProcessFacade.execute, WorkspaceFacade.readBytes, portType.doc
    postReads, failures = [], []
    def process(self, ticket, *, beforeLaunch):
        """Return a frozen synthetic UNKNOWN disposition through the first critical exit."""
        observed = actual(self, ticket, beforeLaunch=beforeLaunch)
        first.processEvidence = replace(observed, status="failed", phase="cleanup", failureCategory="cleanup-unknown",
            cleanup=replace(observed.cleanup, workersSettled=False, disposition="unknown",
                retentionId=ticket.request.requestId, workspaceDisposition="retained"))
        raise first
    def readFile(self, *args, **kwargs):
        """Detect any workspace read attempted after UNKNOWN has been observed."""
        if hasattr(first, "processEvidence"):
            postReads.append(True)
            raise AssertionError("Post-read after UNKNOWN")
        return read(self, *args, **kwargs)
    def failedDoc(self, name, **fields):
        """Fail a later required output after UNKNOWN was frozen without changing its facts."""
        if name == "cli-output@1":
            failures.append(second)
            raise second
        return doc(self, name, **fields)
    monkeypatch.setattr(ProcessFacade, "execute", process)
    monkeypatch.setattr(WorkspaceFacade, "readBytes", readFile)
    monkeypatch.setattr(portType, "doc", failedDoc)
    with pytest.raises(FalsePrimary) as caught:
        launcher.execute(launcher.prepare(definition(), host5(rig[1])), rig[2], handleInterrupt=False)
    assert caught.value is first and failures and not postReads
    value = first.experimentExecution.value()
    run = value["suite"]["units"][0]["run"]
    assert run["processBoundary"] == "unknown" and run["workspaceDisposition"] == "retained"
    assert first.processEvidence.cleanup.disposition == "unknown" and first.processEvidence.exitCode == 1
    assert rig[0]["calls"] == 1 and reader.inspectArchive(value["archive"]["path"])["status"] == "incomplete"
