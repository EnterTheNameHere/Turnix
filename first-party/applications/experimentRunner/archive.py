# file: first-party/applications/experimentRunner/archive.py ; version: 4
"""Launcher-owned bounded archive worker, outside invocation rollback/IO scope."""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import os
import json
from pathlib import Path
from threading import Condition, Event, Thread
import time
from uuid import uuid4

fmt = importlib.import_module("first-party.applications.experimentRunner.archiveFormat")
reader = importlib.import_module("first-party.applications.experimentRunner.archiveReader")


def artifact(raw, *, mediaType="application/json", encoding="utf-8"):
    """Return an immutable byte artifact and detached digest-only reference."""
    fmt.require(type(raw) is bytes)
    ref = dict(schemaId=fmt.PREFIX + "artifact-ref@1", sha256=fmt.digest(raw),
        bytes=len(raw), mediaType=mediaType, encoding=encoding)
    return ref, raw


def document(schemaId, **facts):
    """Create one versioned private JSON artifact, never a filesystem path."""
    return artifact(fmt.canonical(dict(schemaId=fmt.PREFIX + schemaId, **facts)))


def preflight(definition, policy, caps, *, observation=False):
    """Reserve worst-case records/escaped bounded data before archive or model startup."""
    fmt.limits(caps)
    value = definition.value()
    units = len(value["configurations"]) * value["repetitions"]
    turns = units * len(value["conversation"]["turns"])
    records = 8 + units * 2 + turns * (caps["partialRecords"] + 7) + (24 if observation else 0)
    fmt.require(records <= caps["logicalRecords"])
    chars = policy["maxOutputCharacters"]
    conversation = value["conversation"]["turns"]
    query = sum(len(fmt.canonical(turn["content"])) + 64 for turn in conversation)
    query += 6 * chars * (len(conversation) - 1) + 4096
    # Size bound only, never request evidence or a provider payload reconstruction.
    bodyBound = sum(len(json.dumps(turn["content"], ensure_ascii=True).encode("utf-8")) + 64 for turn in conversation)
    bodyBound += 12 * chars * (len(conversation) - 1) + 8192
    if policy.get("captureRequired", True):
        fmt.require(bodyBound <= caps["requestBytes"])
    partial = caps["pendingBytes"]
    reserve = records * caps["frameBytes"] + turns * (caps["requestBytes"] + query + 6 * chars + 65536)
    reserve += turns * caps["partialRecords"] * partial + units * 262144 + 1048576
    reserve += 32 * 1024 * 1024 if observation else 0
    fmt.require(reserve <= caps["archiveBytes"] and query <= caps["artifactBytes"])


def directorySync(path):
    """Attempt directory fsync where supported; Windows unavailability stays explicit."""
    if os.name == "nt":
        return "unavailable-windows"
    handle = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
    return "confirmed"


def writeAll(handle, raw):
    """Complete partial local writes without duplicating a whole record on failure."""
    pending = memoryview(raw)
    while pending:
        count = handle.write(pending)
        fmt.require(type(count) is int and count > 0)
        pending = pending[count:]
    handle.flush()
    os.fsync(handle.fileno())


class ArchiveWriter:
    """One worker owns handles until completion/exit; timeout never steals its IO."""

    def __init__(self, root, suite, caps, cancel, *, cancelled=None, version=1):
        """Prepare inert ownership so callers can root cleanup before thread startup."""
        self.caps = fmt.limits(caps)
        self.archiveId = uuid4().hex
        self.path = Path(root).absolute() / self.archiveId
        fmt.require(type(version) is int and version in (1, 2, 3))
        self.version = version
        if version == 3:
            self.path = Path(root).absolute() / suite["suiteId"] / "archive"
        self.header = dict(schemaId=fmt.PREFIX + "archive@" + str(version), archiveId=self.archiveId,
            suiteId=suite["suiteId"], createdTimeNs=time.time_ns(), definitionSha256=suite["definitionSha256"],
            format="data-commit-json-lines@" + str(version), durability="local-process-death-os-running", limits=self.caps)
        self.cancel = cancel
        self.forwardCancellation = cancelled
        self.lane = Condition()
        self.command = self.active = self.partial = None
        self.commandHeld = 0
        self.knownRefs = {}
        self.partialFlightBytes = 0
        self.failure = self.primary = None
        self.operationPrimary = None
        self.stopping = self.closed = False
        self.sequence = self.bytes = 0
        self.acknowledgedSequence = 0
        self.previous = None
        self.directorySync = "unavailable"
        self.thread = Thread(target=self._work, name="experiment-archive", daemon=True)

    def start(self):
        """Start after ownership is retained; no directory is overwritten on collision."""
        self.thread.start()

    def rememberPrimary(self, error):
        """Retain an App operation's first exit without poisoning best-effort evidence ACKs."""
        with self.lane:
            if self.operationPrimary is None:
                self.operationPrimary = error

    def fail(self, error):
        """Latch fixed failure and invoke exact-Job cancellation outside queue locks."""
        first = False
        with self.lane:
            selected = error if self.operationPrimary is None else self.operationPrimary
            if self.failure is None:
                self.failure = fmt.ArchiveError("Required archive checkpoint failed.")
                first = True
            if not isinstance(selected, Exception) and self.primary is None:
                self.primary = selected
            self.lane.notify_all()
        if first:
            try:
                self.cancel()
            except BaseException as secondary:
                with self.lane:
                    if self.operationPrimary is None and not isinstance(secondary, Exception) and self.primary is None:
                        self.primary = secondary

    def check(self):
        """Fail future admissions and preserve the first non-Exception exit object."""
        with self.lane:
            failure, primary = self.failure, self.primary
        if primary is not None:
            raise primary
        if failure is not None:
            raise failure

    def checkpoint(self, kind, payload, *, unitId=None, turnId=None, artifacts=(), cancelled=None):
        """Queue one bounded mandatory command and wait cooperatively for durable ACK."""
        try:
            return self._checkpoint(kind, payload, unitId=unitId, turnId=turnId, artifacts=artifacts, cancelled=cancelled)
        except BaseException as error:
            self.fail(error)
        self.check()

    def _checkpoint(self, kind, payload, *, unitId=None, turnId=None, artifacts=(), cancelled=None):
        """Bound and detach host data before admitting one worker command."""
        if self.version == 3:
            module = importlib.import_module("first-party.applications.experimentRunner.procedureArchive")
            return module.checkpoint(self, kind, payload, unitId, turnId, artifacts, cancelled)
        self.check()
        encoded = fmt.canonical(payload)
        fmt.require(len(encoded) <= self.caps["frameBytes"] - 2048 and len(artifacts) <= 4)
        blobs = tuple((dict(ref), raw) for ref, raw in artifacts)
        for ref, raw in blobs:
            fmt.reference(ref, self.caps)
            fmt.require(type(raw) is bytes and len(raw) == ref["bytes"] and fmt.digest(raw) == ref["sha256"])
        command = dict(kind=kind, payload=encoded, unitId=unitId, turnId=turnId,
            artifacts=blobs, done=Event(), ack=None)
        deadline = time.monotonic() + self.caps["ackSeconds"]
        try:
            while True:
                self.check()
                if cancelled is not None:
                    cancelled()
                with self.lane:
                    fmt.require(not self.stopping)
                    if self.command is None:
                        self.command = command
                        self.lane.notify_all()
                        break
                fmt.require(time.monotonic() < deadline)
                command["done"].wait(0.02)
            while not command["done"].wait(0.02):
                self.check()
                if cancelled is not None:
                    cancelled()
                fmt.require(time.monotonic() < deadline)
            self.check()
            fmt.require(command["ack"] is not None)
            self.acknowledgedSequence = command["ack"]["sequence"]
            return command["ack"]
        except BaseException as error:
            self.fail(error)
        self.check()

    def beginPartial(self, unitId, turnId, attemptId):
        """Open one bounded diagnostic buffer; no Context/cancellation facade retained."""
        self.check()
        with self.lane:
            fmt.require(self.partial is None)
            self.partial = dict(unitId=unitId, turnId=turnId, attemptId=attemptId,
                events=0, answerOffset=0, reasoningOffset=0,
                partials=0, pending=None, ending=False)

    def observe(self, event):
        """Latch timer/buffer/event failures; optional observer never emits raw errors."""
        try:
            self.check()
            with self.lane:
                state = self.partial
                fmt.require(state is not None and not state["ending"])
                state["events"] += 1
                fmt.require(state["events"] <= self.caps["turnEvents"])
                answer = event.text if event.eventType == "delta" else None
                reasoning = event.metadata.get("llamaCppReasoning", {}).get("content") if event.eventType == "delta" else None
                fmt.require(answer is None or type(answer) is str)
                fmt.require(reasoning is None or type(reasoning) is str)
                if not answer and not reasoning:
                    return
                pending = state["pending"]
                if pending is None:
                    pending = dict(answerDelta=None, reasoningDelta=None, answerAvailable=False,
                        reasoningAvailable=False, eventFrom=state["events"], eventTo=state["events"],
                        events=0, created=time.monotonic())
                else:
                    pending = dict(pending)
                for key, available, text in (("answerDelta", "answerAvailable", answer), ("reasoningDelta", "reasoningAvailable", reasoning)):
                    if text is not None:
                        pending[key] = (pending[key] or "") + text
                        pending[available] = True
                pending["eventTo"] = state["events"]
                pending["events"] += 1
                size = len(fmt.canonical(pending))
                fmt.require(size <= self.caps["pendingBytes"])
                state["pending"] = pending
                self.lane.notify_all()
        except BaseException as error:
            self.fail(error)

    def endPartial(self):
        """Require worker drain before any completed-turn record or next history turn."""
        self.check()
        with self.lane:
            if self.partial is not None:
                self.partial["ending"] = True
            self.lane.notify_all()

    def _batch(self):
        """Detach at most one partial batch under the lane; no callbacks or IO here."""
        state = self.partial
        if state is None:
            return None
        pending = state["pending"]
        if pending is None:
            if state["ending"]:
                self.partial = None
            return None
        chars = len(pending["answerDelta"] or "") + len(pending["reasoningDelta"] or "")
        if not (state["ending"] or self.command is not None or pending["events"] >= self.caps["partialEvents"] or
                chars >= self.caps["partialCharacters"] or time.monotonic() - pending["created"] >= self.caps["partialSeconds"]):
            return None
        fmt.require(state["partials"] < self.caps["partialRecords"])
        delta = document("partial@1", **{k: pending[k] for k in
            ("answerDelta", "reasoningDelta", "answerAvailable", "reasoningAvailable")})
        fmt.require(len(delta[1]) <= self.caps["pendingBytes"])
        payload = dict(attemptId=state["attemptId"], deltaRef=delta[0], eventFrom=pending["eventFrom"],
            eventTo=pending["eventTo"], answerOffset=state["answerOffset"], reasoningOffset=state["reasoningOffset"], partial=True)
        state["answerOffset"] += len(pending["answerDelta"] or "")
        state["reasoningOffset"] += len(pending["reasoningDelta"] or "")
        state["partials"] += 1
        state["pending"] = None
        self.partialFlightBytes = len(delta[1])
        return dict(kind="partial", payload=fmt.canonical(payload), unitId=state["unitId"],
            turnId=state["turnId"], artifacts=(delta,), done=Event(), ack=None)

    def _initialize(self):
        """Create exclusive files and sync them; collisions are errors, never retries."""
        if self.version == 3:
            self.path.parent.parent.mkdir(parents=True, exist_ok=True)
            reader.regular(self.path.parent.parent, directory=True)
            self.path.parent.mkdir(exist_ok=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        reader.regular(self.path.parent, directory=True)
        self.path.mkdir(exist_ok=False)
        (self.path / "artifacts").mkdir(exist_ok=False)
        raw = fmt.canonical(self.header)
        with (self.path / "manifest.json").open("xb", buffering=0) as target:
            writeAll(target, raw)
        self.bytes = len(raw)
        self.previous = fmt.digest(raw)
        self.grammar = fmt.Grammar(self.header)
        self.directorySync = directorySync(self.path)
        directorySync(self.path.parent)

    def _commit(self, command, journal):
        """Publish/sync blobs then DATA then COMMIT; ACK only after all syncs return."""
        fmt.require(self.sequence < self.caps["logicalRecords"])
        unique = {}
        for ref, raw in command["artifacts"]:
            unique[fmt.refKey(ref) if self.version == 3 else ref["sha256"]] = (ref, raw)
        for ref, raw in unique.values():
            path = self.path / "artifacts" / (ref["sha256"] + ".blob")
            if raw is None:
                fmt.require(self.knownRefs.get(fmt.refKey(ref)) == ref)
                reader.BlobReader(self.path, self.caps)(ref)
            elif path.exists():
                fmt.require(reader.readBytes(path, self.caps["artifactBytes"]) == raw)
            else:
                fmt.require(self.bytes + len(raw) <= self.caps["archiveBytes"])
                with path.open("xb", buffering=0) as target:
                    writeAll(target, raw)
                self.bytes += len(raw)
        directorySync(self.path / "artifacts")
        data = dict(schemaId=fmt.PREFIX + "archive-record@" + str(self.version), sequence=self.sequence + 1,
            previousCommitHash=self.previous, kind=command["kind"], archiveId=self.archiveId,
            suiteId=self.header["suiteId"], unitId=command["unitId"], turnId=command["turnId"],
            observedTimeNs=time.time_ns(), payload=fmt.decode(command["payload"]),
            artifacts=[ref for ref, _ in unique.values()])
        data["dataSha256"] = fmt.hashWithout(data, "dataSha256")
        self.grammar.apply(data, reader.BlobReader(self.path, self.caps))
        commit = dict(schemaId=fmt.PREFIX + "archive-commit@" + str(self.version), sequence=data["sequence"],
            dataSha256=data["dataSha256"], previousCommitHash=self.previous)
        commit["commitSha256"] = fmt.hashWithout(commit, "commitSha256")
        dataRaw, commitRaw = fmt.canonical(data) + b"\n", fmt.canonical(commit) + b"\n"
        fmt.require(len(dataRaw) <= self.caps["frameBytes"] and len(commitRaw) <= 1024)
        fmt.require(self.bytes + len(dataRaw) + len(commitRaw) <= self.caps["archiveBytes"])
        writeAll(journal, dataRaw)
        writeAll(journal, commitRaw)
        self.bytes += len(dataRaw) + len(commitRaw)
        self.sequence, self.previous = data["sequence"], commit["commitSha256"]
        command["ack"] = dict(archiveId=self.archiveId, sequence=self.sequence, commitSha256=self.previous,
            artifacts=[ref["sha256"] for ref, _ in unique.values()])
        if self.version == 3:
            with self.lane:
                self.knownRefs.update({fmt.refKey(ref): dict(ref) for ref, raw in unique.values()})

    def _work(self):
        """Own IO until completion; poison on first failure and never invoke cancel locked."""
        try:
            self._initialize()
            with (self.path / "records.log").open("xb", buffering=0) as journal:
                writeAll(journal, b"")
                directorySync(self.path)
                while True:
                    with self.lane:
                        if self.failure is not None:
                            break
                        batch = self._batch()
                        if batch is None and self.partial is not None and self.partial["ending"]:
                            continue
                        if batch is None:
                            batch = self.command
                            self.command = None
                        if batch is None:
                            if self.stopping:
                                break
                            self.lane.wait(min(0.05, self.caps["partialSeconds"]))
                            continue
                        self.active = batch
                        self.lane.notify_all()
                    self._commit(batch, journal)
                    with self.lane:
                        if self.version == 3:
                            batch["artifacts"] = ()
                            self.commandHeld -= batch.pop("charge", 0)
                        self.active = None
                        self.partialFlightBytes = 0
                        batch["done"].set()
                        self.lane.notify_all()
        except BaseException as error:
            self.fail(error)
        finally:
            with self.lane:
                if self.version == 3:
                    for batch in (self.command, self.active):
                        if batch is not None:
                            batch["artifacts"] = ()
                            self.commandHeld -= batch.pop("charge", 0)
                self.closed = True
                self.lane.notify_all()

    def close(self):
        """Bound waiting; pending means worker/handles still owned, never forcibly stopped."""
        with self.lane:
            self.stopping = True
            if self.partial is not None:
                self.partial["ending"] = True
            self.lane.notify_all()
        if self.thread.ident is not None:
            self.thread.join(self.caps["ackSeconds"])
        return dict(status="pending" if self.thread.is_alive() else "failed" if self.failure is not None else "completed",
            committedSequence=self.sequence, directorySync=self.directorySync,
            ackObservation="caller-observed-through-" + str(self.acknowledgedSequence), writerStopped=not self.thread.is_alive())


class UnitArchivePort:
    """Host-minted typed diagnostics for one unit, with no arbitrary path or Context."""

    def __init__(self, writer, unit, broker, owner):
        """Bind exact suite/unit/turn policy and actual trusted composition caller."""
        self.writer, self.unitId, self.turnIds = writer, unit["unitId"], tuple(t["turnId"] for t in unit["run"]["turns"])
        self.broker, self._owner = broker, owner
        self.turnRefs = []
        self.started = []
        self.attemptId = self.turnId = None
        self.attemptIds, self.callIds, self.observationTurns = {}, {}, {}
        self.observationController = None
        self.configurationIndex, self.repetition = unit["configurationIndex"], unit["repetition"]

    def attemptIdFor(self, turnId):
        """Resolve the immutable planned attempt instead of the port's later active turn."""
        return self.attemptIds[turnId]

    def callIdFor(self, turnId):
        """Use only actual request-evidence call identity; opt-out remains unavailable."""
        return self.callIds.get(turnId)

    def observationRecord(self, kind, observationId, value):
        """ACK versioned per-attempt observations without altering Job/provider outcomes."""
        if kind == "observation-snapshot":
            self.observationTurns[observationId] = value["turnId"]
        turnId = self.observationTurns[observationId]
        blob = artifact(fmt.canonical(value))
        payload = dict(attemptId=self.attemptIdFor(turnId), observationId=observationId)
        payload["observationRef" if kind == "observation-snapshot" else "cleanupRef"] = blob[0]
        self.writer.checkpoint(kind, payload, unitId=self.unitId, turnId=turnId, artifacts=(blob,))

    def check(self):
        """Gate every later turn on sticky host writer failure."""
        self.writer.check()

    def begin(self, turnId, query, options):
        """ACK planned query/options before allowing this exact synchronous call."""
        self.check()
        fmt.require(turnId in self.turnIds)
        self.turnId, self.attemptId = turnId, uuid4().hex
        self.attemptIds[turnId] = self.attemptId
        queryBlob, optionsBlob = artifact(fmt.canonical(query)), artifact(fmt.canonical(options))
        self.writer.checkpoint("turn-planned", dict(queryRef=queryBlob[0], requestedOptionsRef=optionsBlob[0],
            attemptId=self.attemptId), unitId=self.unitId, turnId=turnId, artifacts=(queryBlob, optionsBlob),
            cancelled=self.writer.forwardCancellation)
        self.started.append(turnId)
        self.writer.beginPartial(self.unitId, turnId, self.attemptId)

    @contextmanager
    def capture(self):
        """Locate the host policy scope on the actual CodeEntry execution thread."""
        if self.broker is None:
            yield
            return
        returned = [False]
        try:
            with self.broker.scope(self._owner, correlation=(self.writer.header["suiteId"], self.unitId, self.attemptId), sink=self.evidence):
                yield returned
        except BaseException as error:
            if not returned[0]:
                raise
            self.writer.fail(error)

    def evidence(self, event):
        """Durably ACK exact private provider bytes/phase facts, never reserialize body."""
        self.check()
        self.callIds[self.turnId] = event.callId
        body = None if event.body is None else artifact(event.body, encoding="raw")
        payload = dict(attemptId=self.attemptId, providerCallId=event.callId, phase=event.phase,
            bodyRef=None if body is None else body[0], length=event.length, sha256=event.sha256,
            mayHaveSent=event.mayHaveSent, responseHeadersObserved=event.responseHeadersObserved,
            responseBodyObserved=event.responseBodyObserved, serverConsumption=event.serverConsumption)
        self.writer.checkpoint("request-evidence", payload, unitId=self.unitId, turnId=self.turnId,
            artifacts=() if body is None else (body,),
            cancelled=None if event.phase == "final" else self.writer.forwardCancellation)
        return True

    def observe(self, event):
        """Best-effort callback uses sticky fatal channel, not swallowed exceptions."""
        self.writer.observe(event)

    def completed(self, result):
        """Drain partials and ACK completed answer before it enters subsequent history."""
        self.check()
        self.writer.endPartial()
        value = document("turn-result@1", result=result)
        self.writer.checkpoint("turn-completed", dict(resultRef=value[0]), unitId=self.unitId,
            turnId=self.turnId, artifacts=(value,))
        self.turnRefs.append(value[0])


class ArchiveRun:
    """Host coordinator for one archive; never reconstructs or mutates a Job outcome."""

    def __init__(self, writer, definition, composer, config):
        """Root the worker before start and retain only trusted composition inputs."""
        self.writer, self.definition, self.composer, self.config = writer, definition, composer, config
        self.owner = None
        self.ports, self.unitRefs = {}, []
        self.initialProvenance = None
        self.suiteSequence = None
        self.observationPolicy = None

    def compose(self, snapshot):
        """Delegate actual provider publication then bind the exact loaded caller."""
        self.composer.compose(snapshot)
        selector = self.composer.grants[0].entry
        matches = [owner for identity, owner in snapshot.owners if selector.matches(identity)]
        fmt.require(len(matches) == 1)
        self.owner = matches[0]

    def initial(self, suite, provenance):
        """ACK immutable definition/plan/source/assets before any RuntimeHost activation."""
        self.writer.start()
        definition = artifact(self.definition.data)
        plan = document("plan@1", units=[dict(unitId=u["unitId"], configurationIndex=u["configurationIndex"],
            repetition=u["repetition"], profileAlias=u["profileAlias"], turnIds=[t["turnId"] for t in u["run"]["turns"]]) for u in suite["units"]])
        self.initialProvenance = document("provenance@1", facts=provenance)
        payload = dict(definitionRef=definition[0], planRef=plan[0], initialProvenanceRef=self.initialProvenance[0])
        artifacts = (definition, plan, self.initialProvenance)
        if self.writer.version == 2:
            fmt.require(self.observationPolicy is not None)
            policy = artifact(fmt.canonical(self.observationPolicy))
            payload["observationPolicyRef"] = policy[0]
            artifacts += (policy,)
        self.writer.checkpoint("suite-planned", payload, artifacts=artifacts)

    def unit(self, unit, jobId):
        """ACK planned Job and unit options before prepare/runJob and model transition."""
        self.writer.check()
        options = artifact(fmt.canonical(self.definition.value()["configurations"][unit["configurationIndex"] - 1]))
        self.writer.checkpoint("unit-planned", dict(jobId=jobId, configurationIndex=unit["configurationIndex"],
            repetition=unit["repetition"], profileAlias=unit["profileAlias"], requestedOptionsRef=options[0],
            provenanceRef=self.initialProvenance[0]), unitId=unit["unitId"], artifacts=(options, self.initialProvenance),
            cancelled=self.writer.forwardCancellation)
        port = UnitArchivePort(self.writer, unit, self.composer.requestEvidence, self.owner)
        self.ports[unit["unitId"]] = port
        return port

    def unitOutcome(self, unit):
        """Persist original Job/lifetime/reservation facts after the runtime boundary."""
        self.writer.check()
        port = self.ports[unit["unitId"]]
        self.writer.endPartial()
        value = document("unit-summary@1", unitId=unit["unitId"], status=unit["status"],
            jobOutcome=unit["jobOutcome"], lifetimeBoundary=unit["lifetimeBoundary"],
            reservation=unit["run"]["reservation"], configurationEvidence=unit["configurationEvidence"], turnResults=port.turnRefs,
            unstarted=[t for t in port.turnIds if t not in port.started])
        self.writer.checkpoint("unit-outcome", dict(resultRef=value[0]), unitId=unit["unitId"], artifacts=(value,))
        self.unitRefs.append(value[0])

    def suiteOutcome(self, suite):
        """ACK aggregate diagnostics without duplicating transcripts or granting recovery."""
        self.writer.check()
        value = document("suite-summary@1", status=suite["status"], unitResults=self.unitRefs,
            unstarted=[u["unitId"] for u in suite["units"] if u["unitId"] not in self.ports],
            startedTimeNs=suite["startedTimeNs"], endedTimeNs=suite["endedTimeNs"], durationNs=suite["durationNs"])
        self.suiteSequence = self.writer.checkpoint("suite-outcome", dict(summaryRef=value[0]), artifacts=(value,))["sequence"]

    def cleanup(self, component, outcome, facts):
        """Append later independent cleanup observations, never rewrite original outcomes."""
        blob = artifact(fmt.canonical(facts))
        self.writer.checkpoint("cleanup-observed", dict(component=component, observedOutcome=outcome,
            evidenceRef=blob[0]), artifacts=(blob,))

    def seal(self, status):
        """Commit final marker; it does not claim the subsequent close or ACK return."""
        if self.suiteSequence is not None:
            self.writer.checkpoint("archive-seal", dict(lastObservedSuiteStatus=status,
                terminalSequence=self.suiteSequence, finalizationObserved="not-yet-closed"))
