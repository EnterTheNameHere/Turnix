# file: first-party/applications/experimentRunner/reporting.py ; version: 4
"""Offline, bounded comparisons and descriptive reports over validated archives."""
from __future__ import annotations

import base64
from enum import Enum
import importlib
import os
from pathlib import Path

reader = importlib.import_module("first-party.applications.experimentRunner.archiveReader")
reportSchema = importlib.import_module("first-party.applications.experimentRunner.reportSchema")
fmt = reader.fmt
MIB = 1024 * 1024
BASE_REF = "f7e95c536944e25abf3ff79652cbb18d9e8cce44"
GENERATOR = dict(implementationId="applications.experimentRunner.reporting", sourceVersion=4,
                 applicationBaseRef=BASE_REF)
MODES = tuple(fmt.PREFIX + item + "@1" for item in ("answer-text", "reasoning-text", "request-body"))
METRICS = (("application.turn-duration-ns@1", "nanoseconds"),
           ("native.completion-tokens@1", "tokens"),
           ("pm.first-text-delivery-latency-ns@1", "nanoseconds"),
           ("pm.delivered-unicode-characters@1", "unicode-code-points"))


class EvidenceComparison(str, Enum):
    """Closed evidence result; unavailable evidence never becomes boolean equality."""

    EQUAL = "Equal"
    NOT_EQUAL = "NotEqual"
    NOT_COMPARABLE = "NotComparable"


class ReportError(ValueError):
    """Fixed content-free diagnostic for input, selection and workspace failures."""


class SourceFailure(ReportError):
    """Carry one closed source-failure reason without the original private exception."""

    def __init__(self, reason):
        """Store only a fixed public category, with no path or underlying error."""
        super().__init__("Source is unavailable for reporting.")
        self.reason = reason


def require(condition):
    """Reject invalid report inputs without embedding paths, payloads or exceptions."""
    if not condition:
        raise ReportError("Report input or bound is invalid.")


class Sources:
    """Account for cumulative source budgets before invoking the full archive reader."""

    def __init__(self):
        """Start one command's finite source, view and selected workspace budgets."""
        self.count = self.declared = self.actual = self.views = self.private = self.rows = 0

    def open(self, directory):
        """Validate the unfiltered prefix once and bind its exact committed boundary."""
        self.count += 1
        require(self.count <= 2)
        root = Path(directory)
        reader.regular(root, directory=True)
        raw = reader.readManifest(root)
        header = fmt.decode(raw)
        if type(header) is dict and header.get("schemaId") not in tuple(fmt.PREFIX + "archive@" + str(i) for i in (1, 2, 3)):
            raise SourceFailure("unsupported-version")
        fmt.manifest(header)
        require(raw == fmt.canonical(header))
        caps = header["limits"]
        self.declared += caps["archiveBytes"]
        self.views += caps["inspectBytes"]
        require(self.declared <= 512 * MIB and caps["inspectBytes"] <= 64 * MIB and self.views <= 128 * MIB)
        count = 0
        for base in (root, root / "artifacts"):
            reader.regular(base, directory=True)
            with os.scandir(base) as entries:
                for item in entries:
                    count += 1
                    require(count <= caps["logicalRecords"] * (32 if header["schemaId"].endswith("@3") else 4) + 8)
                    if item.is_dir(follow_symlinks=False):
                        require(base == root and item.name == "artifacts")
                    else:
                        self.actual += reader.regular(Path(item.path))
                        require(self.actual <= 512 * MIB)
        view = reader.inspectArchive(root)
        require(view["committedSequence"] == (view["records"][-1]["sequence"] if view["records"] else 0))
        commit = None
        if view["records"]:
            last = view["records"][-1]
            commit = dict(schemaId=fmt.PREFIX + "archive-commit@" + header["schemaId"].rsplit("@", 1)[1],
                          sequence=last["sequence"], dataSha256=last["dataSha256"],
                          previousCommitHash=last["previousCommitHash"])
            commit = fmt.digest(fmt.canonical(commit))
        binding = dict(schemaId=fmt.PREFIX + "source@1", archiveId=header["archiveId"], suiteId=header["suiteId"],
                       manifestSha256=fmt.digest(raw), committedSequence=view["committedSequence"], commitSha256=commit,
                       archiveStatus=view["status"], suiteOutcome=view["lastObservedSuiteStatus"],
                       validation="invalid-prefix" if view["status"] == "corrupt" else "validated-prefix")
        return Source(root, header, view, binding, self)


class Source:
    """Hold only an immutable validated view and digest-based private read authority."""

    def __init__(self, root, header, view, binding, budgets):
        """Index validated records without reparsing the journal or importing backend."""
        self.root, self.header, self.view, self.binding, self.budgets = root, header, view, binding, budgets
        self.definition = None
        self.provenanceHash = None
        self.byTurn = {}
        self.blobs = reader.BlobReader(root, header["limits"])
        for record in view["records"]:
            if record["kind"] == "suite-planned":
                self.definition = self.document(record["payload"]["definitionRef"])
                self.provenanceHash = record["payload"]["initialProvenanceRef"]["sha256"]
            key = record["unitId"], record["turnId"]
            self.byTurn.setdefault(key, []).append(record)
        budgets.rows += sum(len(unit["turns"]) for unit in view["units"])
        require(budgets.rows <= 512)

    def raw(self, ref):
        """Reserve bounded workspace before an exact selected blob read; never cache it."""
        self.budgets.private += ref["bytes"]
        require(self.budgets.private <= 48 * MIB)
        self.blobs.cache.clear()
        return self.blobs(ref)

    def document(self, ref):
        """Decode only an already reader-validated digest-bound JSON artifact."""
        return fmt.decode(self.raw(ref))

    def selected(self, unitId, turnId):
        """Resolve one exact explicit selection; never pair by position or labels."""
        units = [unit for unit in self.view["units"] if unit["unitId"] == unitId]
        require(len(units) == 1)
        turns = [turn for turn in units[0]["turns"] if turn["turnId"] == turnId]
        require(len(turns) == 1 and self.definition is not None)
        records = self.byTurn.get((unitId, turnId), [])
        result = next((self.document(r["payload"]["resultRef"])["result"]
                       for r in records if r["kind"] == "turn-completed"), None)
        body = next((r["payload"]["bodyRef"] for r in records
                     if r["kind"] == "request-evidence" and r["payload"]["phase"] == "serialized"), None)
        planned = next((r["payload"] for r in records if r["kind"] == "turn-planned"), None)
        observation = None
        for record in records:
            if record["kind"] == "observation-snapshot":
                candidate = self.document(record["payload"]["observationRef"])
                if candidate["phase"] == "call-final":
                    observation = candidate
                    break
        procedure = self.definition["schemaId"] == fmt.PREFIX + "definition@3"
        turns = ([s for s in self.definition["procedure"] if s["op"] == "inference"] if procedure else self.definition["conversation"]["turns"])
        ordinal = next(i for i, turn in enumerate(turns) if turn["id"] == turnId)
        config = self.definition["configurations"][units[0]["configurationIndex"] - 1]
        prefix = turns[:ordinal + 1]
        signature = fmt.digest(fmt.canonical(dict(ordinal=ordinal + 1,
            historyPolicy=self.definition["conversation"]["historyPolicy"], contextPolicy=self.definition["contextPolicy"],
            messages=[dict(role="user", content=t["content"]) for t in prefix]))) if not procedure else fmt.digest(fmt.canonical(dict(
                ordinal=ordinal + 1, **self.view["procedureIdentity"], querySha256=None if planned is None else planned["queryRef"]["sha256"])))
        queryHash = None if planned is None else planned["queryRef"]["sha256"]
        optionsHash = None if planned is None else planned["requestedOptionsRef"]["sha256"]
        return dict(unit=units[0], turn=turns[0], result=result, body=body, records=records,
                    ordinal=ordinal + 1, config=config, inputSignature=signature,
                    querySha256=queryHash, optionsSha256=optionsHash, observation=observation)


def availability(selected):
    """Separate missing reasoning/body from empty text and failed-turn partial data."""
    value = selected["result"]
    succeeded = value is not None and value["status"] == "succeeded"
    return dict(answer=succeeded and type(value["answer"]) is str,
                reasoning=succeeded and type(value["reasoning"]) is str, requestBody=selected["body"] is not None)


def field(source, selected, mode):
    """Read exactly one requested evidence mode without weaker fallbacks."""
    present = availability(selected)
    if mode == MODES[2]:
        return (source.raw(selected["body"]), None) if present["requestBody"] else (None, "body-unavailable")
    name = "answer" if mode == MODES[0] else "reasoning"
    if selected["result"] is None or selected["result"]["status"] != "succeeded":
        return None, "turn-not-completed"
    return (selected["result"][name], None) if present[name] else (None, name + "-unavailable")


def compare(left, leftUnit, leftTurn, right, rightUnit, rightTurn, *, mode, allowInputDifferences=False):
    """Compare a single explicit pair with closed three-way results and disclosed inputs."""
    require(mode in MODES and type(allowInputDifferences) is bool)
    result = dict(schemaId=fmt.PREFIX + "comparison@1", generator=dict(GENERATOR), sources=[],
                  selection=[dict(sourceIndex=0, unitId=leftUnit, turnId=leftTurn),
                             dict(sourceIndex=1, unitId=rightUnit, turnId=rightTurn)], mode=mode,
                  result=EvidenceComparison.NOT_COMPARABLE.value, reasons=[], alignment=None,
                  fieldAvailability=[], difference=None, sourceOutcomes=[], contentPolicy="summary-only", content=[])
    budgets = Sources()
    sources = []
    for directory in (left, right):
        try:
            source = budgets.open(directory)
        except Exception as error:
            reason = error.reason if isinstance(error, SourceFailure) else "input-limit" if isinstance(error, ReportError) else "source-unavailable" if isinstance(error, OSError) else "source-invalid"
            result["reasons"].append(reason)
            return result
        sources.append(source)
        result["sources"].append(source.binding)
        result["sourceOutcomes"].append(dict(archiveStatus=source.view["status"], suiteOutcome=source.view["lastObservedSuiteStatus"]))
    if any(s.header["schemaId"].endswith("@3") for s in sources):
        result["schemaId"] = fmt.PREFIX + "comparison@2"
        result["procedureQualification"] = dict(leftProcedureSha256=None, rightProcedureSha256=None,
            leftInputsSha256=None, rightInputsSha256=None, leftQuerySha256=None, rightQuerySha256=None,
            differencesAllowed=allowInputDifferences)
        for side, source in zip(("left", "right"), sources, strict=True):
            identity = source.view.get("procedureIdentity", {})
            result["procedureQualification"][side + "ProcedureSha256"] = identity.get("procedureSha256")
            result["procedureQualification"][side + "InputsSha256"] = identity.get("inputsSha256")
    if any(source.view["status"] == "corrupt" for source in sources):
        result["reasons"] = ["source-invalid"]
        return result
    try:
        selected = [sources[0].selected(leftUnit, leftTurn), sources[1].selected(rightUnit, rightTurn)]
    except Exception:
        result["reasons"] = ["selection-missing"]
        return result
    result["fieldAvailability"] = [availability(item) for item in selected]
    if "procedureQualification" in result:
        for side, item in zip(("left", "right"), selected, strict=True):
            result["procedureQualification"][side + "QuerySha256"] = item["querySha256"]
    result["alignment"] = dict(inputSignatures=[item["inputSignature"] for item in selected],
        querySha256=[item["querySha256"] for item in selected], requestedOptionsSha256=[item["optionsSha256"] for item in selected],
        configurationSha256=[fmt.digest(fmt.canonical(item["config"])) for item in selected],
        provenanceSha256=[source.provenanceHash for source in sources],
        allowInputDifferences=allowInputDifferences,
        sameObservation=sources[0].binding == sources[1].binding and leftUnit == rightUnit and leftTurn == rightTurn)
    if selected[0]["inputSignature"] != selected[1]["inputSignature"] and not allowInputDifferences:
        result["reasons"] = ["input-signature-mismatch"]
        return result
    values = [field(source, item, mode) for source, item in zip(sources, selected, strict=True)]
    result["reasons"] = list(dict.fromkeys(reason for _, reason in values if reason is not None))
    if result["reasons"]:
        return result
    a, b = [value for value, _ in values]
    different = next((index for index, (x, y) in enumerate(zip(a, b)) if x != y), None)
    if different is None and len(a) != len(b):
        different = min(len(a), len(b))
    rawA, rawB = (a, b) if mode == MODES[2] else (a.encode("utf-8"), b.encode("utf-8"))
    result["result"] = EvidenceComparison.EQUAL.value if different is None else EvidenceComparison.NOT_EQUAL.value
    result["reasons"] = ["field-available"]
    result["difference"] = dict(unit="bytes" if mode == MODES[2] else "unicode-code-points",
                                lengths=[len(a), len(b)], sha256=[fmt.digest(rawA), fmt.digest(rawB)], firstDifference=different)
    return result


def metric(metricId, unit, value, reason, provenance):
    """Represent known integer measurements as decimals, keeping absence explicit."""
    known = type(value) is int and value >= 0 and value <= 2 ** 63 - 1
    return dict(schemaId=fmt.PREFIX + "metric@1", metricId=metricId, unit=unit,
                value=str(value) if known else None, availability="available" if known else "unavailable",
                reason="measured" if known else reason, measurementProvenance=provenance)


def metrics(selected):
    """Keep App software intervals distinct from native reported completion-token counts."""
    value = selected["result"]
    duration = None if value is None else value["durationNs"]
    native = {} if value is None or type(value["native"]) is not dict else value["native"]
    usage = native.get("usage")
    tokens = usage.get("completion_tokens") if type(usage) is dict else None
    result = [metric(*METRICS[0], duration, "not-completed", "app-monotonic-preparation-through-call-cleanup-before-turn-ack"),
            metric(*METRICS[1], tokens, "native-usage-unavailable", "provider-reported-completion-tokens")]
    observation = selected["observation"]
    data = None if observation is None else observation["publicData"]
    stream = None if data is None or data["streamAvailability"] == "unavailable" else data["stream"]
    for definition, key in ((METRICS[2], "firstTextLatencyNs"), (METRICS[3], "characterCount")):
        measured = None if stream is None or stream["availability"] != "available" or stream["originNs"] is None else stream[key]
        provenance = dict(definition="pm-stream-delivery@1", phase="call-final",
                          sessionId=None if observation is None else observation["sessionId"],
                          clockEpochId=None if observation is None else observation["clockEpochId"])
        result.append(metric(*definition, None if measured is None else int(measured), "observation-unavailable", provenance))
    return result


def aggregate(rows, metricId, unit):
    """Compute descriptive rational aggregates with explicit exclusions and no zero fill."""
    values, reasons = [], {}
    for row in rows:
        item = next(m for m in row["metrics"] if m["metricId"] == metricId)
        if item["availability"] == "available":
            values.append(int(item["value"]))
        else:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    total = sum(values)
    return dict(schemaId=fmt.PREFIX + "aggregate@1", metricId=metricId, unit=unit, contributing=len(values),
                excluded=len(rows) - len(values), exclusionReasons=reasons, sum=str(total) if values else None,
                min=str(min(values)) if values else None, max=str(max(values)) if values else None,
                mean=dict(numerator=str(total), denominator=str(len(values))) if values else None)


def report(directory, *, unitIds=(), turnIds=(), includeContent=False, includeObservations=False):
    """Describe selected planned rows without silently dropping incomplete or failed work."""
    require(type(includeContent) is bool and type(includeObservations) is bool and len(unitIds) == len(set(unitIds)) and len(turnIds) == len(set(turnIds)))
    source = Sources().open(directory)
    units = source.view["units"]
    require(set(unitIds) <= {u["unitId"] for u in units})
    require(set(turnIds) <= {t["turnId"] for u in units for t in u["turns"]})
    selectedPairs = [(u, t) for u in units if not unitIds or u["unitId"] in unitIds
                     for t in u["turns"] if not turnIds or t["turnId"] in turnIds]
    require(len(selectedPairs) <= 256 and len(units) <= 32)
    require(not (includeContent or includeObservations) or len(selectedPairs) == len(unitIds) == len(turnIds) == 1)
    rows, content = [], []
    for unit, turn in selectedPairs:
        selected = source.selected(unit["unitId"], turn["turnId"])
        config = selected["config"]
        rows.append(dict(schemaId=fmt.PREFIX + "row@1", sourceIndex=0, unitId=unit["unitId"],
            configurationIndex=unit["configurationIndex"], configurationName=config["name"], profileAlias=config["profile"],
            repetition=unit["repetition"], turnId=turn["turnId"], turnOrdinal=selected["ordinal"],
            unitStatus=unit["lastObservedStatus"], jobOutcome=unit["jobOutcome"], lifetimeBoundary=unit["lifetimeBoundary"],
            turnStatus=turn["lastObservedStatus"], callCleanup=cleanCleanup(turn["cleanup"]), fieldAvailability=availability(selected),
            metrics=metrics(selected), observationAvailability="available" if selected["observation"] is not None and
                selected["observation"]["availability"] == "available" else "unavailable"))
        if includeContent or includeObservations:
            body = None if not includeContent or selected["body"] is None else source.raw(selected["body"])
            require(body is None or 4 * ((len(body) + 2) // 3) <= 32 * MIB)
            value = selected["result"] if includeContent else None
            content.append(dict(unitId=unit["unitId"], turnId=turn["turnId"],
                                answer=None if value is None else value["answer"], reasoning=None if value is None else value["reasoning"],
                                observations=[] if not includeObservations or selected["observation"] is None else [selected["observation"]],
                                requestBody=None if body is None else dict(encoding="base64", bytes=len(body), sha256=fmt.digest(body),
                                                                         value=base64.b64encode(body).decode("ascii"))))
    statuses = {}
    for row in rows:
        statuses[row["turnStatus"]] = statuses.get(row["turnStatus"], 0) + 1
    records = [r for r in source.view["records"] if (r["unitId"], r["turnId"]) in
               {(u["unitId"], t["turnId"]) for u, t in selectedPairs}]
    counts = dict(plannedTurns=None if source.definition is None else len(selectedPairs), turnStatuses=statuses,
                  transportAttempts=sum(r["kind"] == "request-evidence" and r["payload"]["phase"] == "transport-attempt" for r in records),
                  finalMayHaveSent=sum(r["kind"] == "request-evidence" and r["payload"]["phase"] == "final"
                                       and r["payload"]["mayHaveSent"] is True for r in records))
    value = dict(schemaId=fmt.PREFIX + "report@1", generator=dict(GENERATOR), sources=[source.binding],
                selection=dict(unitIds=list(unitIds), turnIds=list(turnIds)),
                scopeCompleteness="invalid-prefix" if source.view["status"] == "corrupt" else
                    "sealed-source" if source.view["status"] == "sealed" else "incomplete-prefix",
                counts=counts, rows=rows, metrics=[aggregate(rows, *item) for item in METRICS],
                contentPolicy="selected-private" if includeContent or includeObservations else "summary-only", content=content,
                diagnostics=dict(ackObservation="unavailable", writerCloseObservation="unavailable",
                                 aggregateStatusStrata=statuses))
    if source.header["schemaId"].endswith("@3"):
        module = importlib.import_module("first-party.applications.experimentRunner.procedureReporting")
        module.extend(value, source, unitIds, includeContent)
    return value


def cleanCleanup(value):
    """Preserve actual action status separately from quiescence and absent evidence."""
    status = value.get("status")
    status = "unavailable" if status is None else status if status in reportSchema.CLEANUP_STATUSES else "unknown"
    return dict(status=status,
                quiescence="confirmed" if value.get("quiescence") == "confirmed" else "unknown")
