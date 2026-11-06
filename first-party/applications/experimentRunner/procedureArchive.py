# file: first-party/applications/experimentRunner/procedureArchive.py ; version: 1
"""Host-owned archive3 ports/queue reservations; never retain invocation authority."""
from __future__ import annotations

import importlib
from threading import Event, RLock
import time
from uuid import uuid4

archive = importlib.import_module("first-party.applications.experimentRunner.archive")
fmt, reader = archive.fmt, archive.reader
pf = importlib.import_module("first-party.applications.experimentRunner.procedureFormat")
inputs = importlib.import_module("first-party.applications.experimentRunner.inputs")


def checkpoint(writer, kind, payload, unitId, turnId, artifacts, cancelled):
    """Reserve active/queued blob and frame bytes before detaching a mandatory command."""
    fmt.require(len(artifacts) <= 32)
    for ref, raw in artifacts:
        fmt.reference(ref, writer.caps)
    charge = writer.caps["frameBytes"] + sum(ref["bytes"] for ref, raw in artifacts)
    fmt.require(charge <= writer.caps["commandBytes"])
    deadline = time.monotonic() + writer.caps["ackSeconds"]
    published, reserved, command = False, False, None
    try:
        while not reserved:
            writer.check()
            if cancelled is not None:
                cancelled()
            with writer.lane:
                fmt.require(not writer.stopping and not writer.closed)
                if writer.commandHeld + charge <= writer.caps["commandBytes"]:
                    writer.commandHeld += charge
                    reserved = True
                else:
                    writer.lane.wait(.02)
            fmt.require(time.monotonic() < deadline)
        encoded = fmt.canonical(payload)
        fmt.require(len(encoded) <= writer.caps["frameBytes"] - 2048)
        blobs = []
        for ref, raw in artifacts:
            if raw is None:
                with writer.lane:
                    fmt.require(writer.knownRefs.get(fmt.refKey(ref)) == ref)
            else:
                fmt.require(type(raw) is bytes and len(raw) == ref["bytes"] and fmt.digest(raw) == ref["sha256"])
            blobs.append((dict(ref), raw))
        command = dict(kind=kind, payload=encoded, unitId=unitId, turnId=turnId,
                       artifacts=tuple(blobs), done=Event(), ack=None, charge=charge)
        while not published:
            writer.check()
            if cancelled is not None:
                cancelled()
            with writer.lane:
                fmt.require(not writer.stopping)
                if writer.command is None:
                    writer.command = command
                    published = True
                    writer.lane.notify_all()
            fmt.require(time.monotonic() < deadline)
            if not published:
                command["done"].wait(.02)
        while not command["done"].wait(.02):
            writer.check()
            if cancelled is not None:
                cancelled()
            fmt.require(time.monotonic() < deadline)
        writer.check()
        fmt.require(command["ack"] is not None)
        writer.acknowledgedSequence = command["ack"]["sequence"]
        return command["ack"]
    finally:
        if reserved and not published:
            with writer.lane:
                writer.commandHeld -= charge
                writer.lane.notify_all()


class Port(archive.UnitArchivePort):
    """Typed unit diagnostics with frozen run inputs and no Context/process authority."""

    def __init__(self, writer, unit, broker, owner, run):
        """Bind exact plan/attempt identities; share immutable input bytes once per run."""
        super().__init__(writer, unit, broker, owner)
        self.run, self.inputData = run, run.frozen.data()
        self.attemptIds = {a["turnId"]: a["attemptId"] for a in run.planUnits[unit["unitId"]]["attemptIds"]}
        self.steps = run.definition.value()["procedure"]
        self.pending, self.lane = {}, RLock()
        self.requests, self.requestValues, self.outcomes, self.admissions, self.turnMap = {}, {}, {}, {}, {}
        self.intents, self.completedSteps = [], []
        self.current = None

    def put(self, raw, *, json=False):
        """Stage bounded immutable bytes until a particular command takes ownership."""
        ref, data = archive.artifact(raw, mediaType="application/json" if json else "application/octet-stream",
                                     encoding="utf-8" if json else "raw")
        fmt.reference(ref, self.writer.caps)
        with self.lane:
            self.pending[fmt.refKey(ref)] = ref, data
        return ref

    def doc(self, name, **fields):
        """Create one exact versioned private document as a detached blob."""
        return self.put(fmt.canonical(dict(schemaId=fmt.PREFIX + name, **fields)), json=True)

    def commit(self, kind, payload, refs, *, turnId=None):
        """Detach only needed references outside locks before mandatory archive ACK."""
        unique = {fmt.refKey(ref): ref for ref in refs if ref is not None}
        with self.lane:
            artifacts = tuple(self.pending.pop(key, (dict(ref), None)) for key, ref in unique.items())
        return self.writer.checkpoint(kind, payload, unitId=self.unitId, turnId=turnId, artifacts=artifacts)

    def planned(self, step, value, refs):
        """ACK one resolved logical intent before its irreversible operation."""
        ref = self.doc("step-request@1", **value)
        self.commit("step-planned", dict(opId=step["id"], ordinal=len(self.intents) + 1,
            op=step["op"], requestRef=ref), (ref, *refs))
        self.requests[step["id"]], self.requestValues[step["id"]] = ref, value
        self.intents.append(step["id"])
        self.current = step["id"]
        return ref

    def admitted(self, opId, request):
        """Run on shared ACK supervisor with port only; never call/reenter runtime."""
        fmt.require(self.current == opId)
        ref = self.put(fmt.canonical(request.snapshot()), json=True)
        self.commit("process-admitted", dict(opId=opId, processRequestRef=ref), (ref,))
        with self.lane:
            self.admissions[opId] = ref
        return True

    def terminal(self, step, value, refs):
        """ACK actual success/failure before allowing the next step or history append."""
        ref = self.doc("step-outcome@1", **value)
        self.commit("step-completed", dict(opId=step["id"], outcomeRef=ref), (ref, *refs))
        self.outcomes[step["id"]] = ref
        self.completedSteps.append(dict(opId=step["id"], outcomeRef=ref))
        self.current = None
        return ref

    def begin(self, turnId, query, options):
        """ACK exact query/options with the preallocated immutable attempt identity."""
        self.check()
        self.turnId, self.attemptId = turnId, self.attemptIds[turnId]
        queryBlob = archive.artifact(fmt.canonical(query))
        optionsBlob = archive.artifact(fmt.canonical(options))
        self.writer.checkpoint("turn-planned", dict(queryRef=queryBlob[0], requestedOptionsRef=optionsBlob[0],
            attemptId=self.attemptId), unitId=self.unitId, turnId=turnId, artifacts=(queryBlob, optionsBlob),
            cancelled=self.writer.forwardCancellation)
        self.started.append(turnId)
        self.writer.beginPartial(self.unitId, turnId, self.attemptId)

    def completed(self, result):
        """Retain original per-call capture semantics with explicitly versioned turn2."""
        self.check()
        self.writer.endPartial()
        value = archive.document("turn-result@2", result=result)
        self.writer.checkpoint("turn-completed", dict(resultRef=value[0]), unitId=self.unitId,
                              turnId=self.turnId, artifacts=(value,))
        self.turnRefs.append(value[0])
        self.turnMap[self.turnId] = value[0]


class Run(archive.ArchiveRun):
    """Own one archive3 run and shared input snapshot, independent of Job rollback."""

    def __init__(self, writer, definition, composer, config):
        """Retain inert host inputs before any archive worker/runtime starts."""
        super().__init__(writer, definition, composer, config)
        self.frozen = composer.procedureInputs
        self.planUnits = {}
        self.inputRefs = {}

    def initial(self, suite, provenance):
        """ACK raw/frozen sources then ordered unit plan before runtime activation."""
        self.writer.start()
        rawDef = archive.artifact(self.frozen.original, mediaType="application/octet-stream", encoding="raw")
        definition = archive.artifact(self.definition.data)
        entries, inputBlobs = [], []
        for name, relative, raw, observation in self.frozen.files:
            item = archive.artifact(raw, mediaType="application/octet-stream", encoding="raw")
            inputBlobs.append(item)
            self.inputRefs[name] = item[0]
            entries.append(dict(inputId=name, sourceRelativePath=relative, rawRef=item[0], observation=dict(zip(
                ("observedTimeNs", "device", "inode", "size", "mtimeNs", "ctimeNs", "sha256"), observation))))
        index = archive.document("inputs-index@1", entries=entries)
        interpreter = archive.document("interpreter-policy@1", definitionFormat=self.frozen.format,
            parser=inputs.parserIdentity(self.frozen.format), normalization="sorted-compact-utf8-json",
            limits=self.config["procedureLimits"], sourceIdentity=[{k: s[k] for k in ("path", "sha256")}
                for s in provenance["sources"] if s["path"].startswith("first-party/applications/experimentRunner/")])
        self.writer.checkpoint("run-inputs", dict(originalDefinitionRef=rawDef[0], normalizedDefinitionRef=definition[0],
            inputsIndexRef=index[0], interpreterPolicyRef=interpreter[0]),
            artifacts=(rawDef, definition, index, interpreter, *inputBlobs))
        for unit in suite["units"]:
            self.planUnits[unit["unitId"]] = dict(unitId=unit["unitId"], configurationIndex=unit["configurationIndex"],
                repetition=unit["repetition"], profileAlias=unit["profileAlias"],
                stepIds=[s["id"] for s in self.definition.value()["procedure"]],
                turnIds=[t["turnId"] for t in unit["run"]["turns"]],
                attemptIds=[dict(turnId=t["turnId"], attemptId=uuid4().hex) for t in unit["run"]["turns"]])
        plan = archive.document("procedure-plan@1", procedureSha256=fmt.digest(fmt.canonical(self.definition.value()["procedure"])),
                                units=list(self.planUnits.values()))
        tools = archive.document("tool-policy@1", commands=self.config["approvedCommands"], tools=self.composer.procedureTools)
        provenance = {k: provenance[k] for k in ("sources", "assets", "capturePolicy", "serverConsumption", "providerEffectiveState")}
        provenance.update(platformBaseline=pf.PLATFORM_REF, validationPolicy=pf.policy(self.config), toolPolicyRef=tools[0])
        self.initialProvenance = archive.document("provenance@2", facts=provenance)
        policy = archive.artifact(fmt.canonical(self.observationPolicy))
        self.writer.checkpoint("suite-planned", dict(definitionRef=definition[0], planRef=plan[0],
            initialProvenanceRef=self.initialProvenance[0], observationPolicyRef=policy[0]),
            artifacts=(definition, plan, self.initialProvenance, policy, tools))

    def unit(self, unit, jobId):
        """Bind actual Job identity before preparation/transition and issue only typed port."""
        options = archive.artifact(fmt.canonical(self.definition.value()["configurations"][unit["configurationIndex"] - 1]))
        self.writer.checkpoint("unit-planned", dict(jobId=jobId, configurationIndex=unit["configurationIndex"],
            repetition=unit["repetition"], profileAlias=unit["profileAlias"], requestedOptionsRef=options[0],
            provenanceRef=self.initialProvenance[0]), unitId=unit["unitId"], artifacts=(options, self.initialProvenance),
            cancelled=self.writer.forwardCancellation)
        port = Port(self.writer, unit, self.composer.requestEvidence, self.owner, self)
        self.ports[unit["unitId"]] = port
        return port

    def unitOutcome(self, unit):
        """Persist actual Job plus partial step/CLI/file facts independently."""
        port = self.ports[unit["unitId"]]
        self.writer.endPartial()
        run = unit["run"]
        value = archive.document("unit-summary@2", unitId=unit["unitId"], status=unit["status"],
            jobOutcome=unit["jobOutcome"], lifetimeBoundary=unit["lifetimeBoundary"], reservation=run["reservation"],
            configurationEvidence=unit["configurationEvidence"], turnResults=port.turnRefs,
            stepResults=port.completedSteps, pendingStep=port.current,
            unstarted=[s["id"] for s in port.steps if s["id"] not in port.intents],
            processBoundary=run["processBoundary"], workspaceDisposition=run["workspaceDisposition"])
        self.writer.checkpoint("unit-outcome", dict(resultRef=value[0]), unitId=unit["unitId"], artifacts=(value,))
        self.unitRefs.append(value[0])

    def suiteOutcome(self, suite):
        """Finalize diagnostics without promoting a partial process to successful procedure."""
        value = archive.document("suite-summary@2", status=suite["status"], unitResults=self.unitRefs,
            unstarted=[u["unitId"] for u in suite["units"] if u["unitId"] not in self.ports],
            startedTimeNs=suite["startedTimeNs"], endedTimeNs=suite["endedTimeNs"], durationNs=suite["durationNs"])
        self.suiteSequence = self.writer.checkpoint("suite-outcome", dict(summaryRef=value[0]), artifacts=(value,))["sequence"]
