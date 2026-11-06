# file: first-party/applications/experimentRunner/procedureGrammar.py ; version: 1
"""Offline archive3 semantics; no Context, backend, parser dependency or active IO."""
from __future__ import annotations

import importlib
import re
from pathlib import PurePosixPath, PureWindowsPath

fmt = importlib.import_module("first-party.applications.experimentRunner.archiveFormat")
pf = importlib.import_module("first-party.applications.experimentRunner.procedureFormat")
KEYS = {
    "run-inputs": ("originalDefinitionRef", "normalizedDefinitionRef", "inputsIndexRef", "interpreterPolicyRef"),
    "suite-planned": ("definitionRef", "planRef", "initialProvenanceRef", "observationPolicyRef"),
    "step-planned": ("opId", "ordinal", "op", "requestRef"),
    "process-admitted": ("opId", "processRequestRef"), "step-completed": ("opId", "outcomeRef"),
    "unit-outcome": ("resultRef",), "suite-outcome": ("summaryRef",)}
REVISION = ("artifactId", "revision", "relativePath", "originOpId", "previousRevisionRef", "contentRef", "workspaceObservation")
OUTCOME = ("status", "reason", "startedTimeNs", "endedTimeNs", "durationNs", "outputRef", "error")
PROCESS_REQUEST = ("schemaId", "requestId", "applicationRunId", "codeEntryInstanceId", "invocationId", "jobId",
    "toolName", "configuredExecutable", "resolvedExecutable", "arguments", "workingDirectory", "timeoutSeconds",
    "stdoutBytes", "stderrBytes", "cleanupSeconds", "environmentPolicy", "preparedTimeNs", "executableObservation")
PROCESS_OUTCOME = ("requestRef", "status", "phase", "launchState", "startedTimeNs", "endedTimeNs", "durationNs",
    "exitCode", "stopReason", "stdoutRef", "stderrRef", "stdoutFacts", "stderrFacts", "cleanup", "failureCategory")


def rawText(ref, blob, maximum):
    """Read exact digest-bound UTF-8 bytes without repair or source-file fallback."""
    raw = blob(ref)
    fmt.require(ref["mediaType"] == "application/octet-stream" and ref["encoding"] == "raw")
    value = raw.decode("utf-8", errors="strict")
    pf.text(value, maximum)
    return raw


def request(value, step, unit, grammar):
    """Bind exact process identity/argv/cwd/caps to host policy and actual unit Job."""
    fmt.fields(value, PROCESS_REQUEST)
    fmt.require(value["schemaId"] == "actant.process.request@1")
    for name in ("requestId", "invocationId"):
        fmt.identifier(value[name], opaque=True)
    if not hasattr(grammar, "procRequestIds"):
        grammar.procRequestIds = {}
    previous = grammar.procRequestIds.setdefault(value["requestId"], (grammar.activeUnit, step["id"]))
    fmt.require(previous == (grammar.activeUnit, step["id"]))
    for name in ("applicationRunId", "codeEntryInstanceId", "jobId"):
        fmt.identifier(value[name])
    fmt.require(value["jobId"] == unit["jobId"])
    command = grammar.procPolicy["approvedCommands"][step["command"]]
    tool = next(t for t in grammar.procTools["tools"] if t["toolName"] == command["tool"])
    fmt.require(value["toolName"] == command["tool"] and value["configuredExecutable"] == tool["configuredExecutable"]
        and value["resolvedExecutable"] == tool["observedExecutable"])
    for name in ("timeoutSeconds", "stdoutBytes", "stderrBytes", "cleanupSeconds"):
        fmt.require(type(value[name]) is not bool and value[name] == command[name])
    cwd = value["workingDirectory"]
    pf.text(cwd, 32768)
    pathType = PureWindowsPath if ":" in cwd or "\\" in cwd else PurePosixPath
    fmt.require(pathType(cwd).is_absolute())
    artifactPath = grammar.procArtifacts[step["artifact"]]
    expanded = [str(pathType(cwd) / artifactPath) if type(a) is dict else a for a in command["arguments"]]
    fmt.require(value["arguments"] == expanded and sum(len(a.encode("utf-8")) for a in expanded)
                <= grammar.procPolicy["procedureLimits"]["argvBytes"])
    fmt.require(value["environmentPolicy"] == "inherited-host-plus-configured-overrides-frozen")
    fmt.integer(value["preparedTimeNs"])
    fmt.fields(value["executableObservation"], ("status", "bytes", "sha256", "observedTimeNs"))
    fmt.require(value["executableObservation"] == dict(status="not-hashed", bytes=None, sha256=None,
                                                     observedTimeNs=value["preparedTimeNs"]))
    binding = value["applicationRunId"], value["codeEntryInstanceId"], value["invocationId"], cwd
    if unit["processIdentity"] is None:
        fmt.require(not any(other["processIdentity"] is not None and
            (other["processIdentity"][2] == binding[2] or other["processIdentity"][3].casefold() == cwd.casefold())
            for other in grammar.units.values() if other is not unit))
        unit["processIdentity"] = binding
    fmt.require(unit["processIdentity"] == binding)


def process(value, expected, blob):
    """Validate nullable process/stream/cleanup facts without inventing execution receipts."""
    fmt.fields(value, ("schemaId", *PROCESS_OUTCOME))
    fmt.require(value["schemaId"] == fmt.PREFIX + "process-outcome@1" and value["requestRef"] == expected[0])
    req = expected[1]
    fmt.require(value["status"] in ("completed", "failed", "cancelled", "timeout", "output-limit")
        and value["phase"] in ("admission", "ack", "spawn", "capture", "cleanup", "completed")
        and value["launchState"] in ("not-attempted", "attempted", "started"))
    fmt.require(value["stopReason"] in (None, "cancelled", "timeout", "stdout-limit", "stderr-limit", "execution-failed", "interrupted"))
    fmt.require(value["failureCategory"] in (None, "resolution", "expired", "ack-rejected", "ack-failed",
        "spawn-failed", "capture-failed", "cleanup-unknown", "interrupted"))
    for name in ("endedTimeNs", "durationNs"):
        fmt.integer(value[name])
    fmt.require((value["startedTimeNs"] is not None) == (value["launchState"] == "started"))
    if value["startedTimeNs"] is not None:
        fmt.integer(value["startedTimeNs"])
    if value["exitCode"] is not None:
        fmt.require(type(value["exitCode"]) is int and value["launchState"] != "not-attempted")
    cleanup = value["cleanup"]
    fmt.fields(cleanup, ("scope", "childReaped", "readersJoined", "pipesClosed", "spawnResolved", "ackResolved",
                        "workersSettled", "stopAttempted", "disposition", "retentionId", "workspaceDisposition"))
    fmt.require(cleanup["scope"] == "owned-child-ack-spawn-supervisor-and-capture-workers")
    for name in ("childReaped", "readersJoined", "pipesClosed", "spawnResolved", "ackResolved", "workersSettled", "stopAttempted"):
        fmt.require(type(cleanup[name]) is bool)
    fmt.require(cleanup["disposition"] in ("confirmed", "unknown") and cleanup["workspaceDisposition"]
        in ("unaffected", "released-pin", "retained"))
    if cleanup["disposition"] == "confirmed":
        fmt.require(all(cleanup[k] for k in ("childReaped", "readersJoined", "pipesClosed", "spawnResolved", "ackResolved", "workersSettled"))
            and cleanup["retentionId"] is None and cleanup["workspaceDisposition"] != "retained")
    else:
        fmt.require(cleanup["retentionId"] == req["requestId"] and not cleanup["workersSettled"]
            and value["failureCategory"] == "cleanup-unknown")
    fmt.require(value["exitCode"] is None or cleanup["childReaped"])
    decoded, complete = {}, True
    for stream in ("stdout", "stderr"):
        raw = blob(value[stream + "Ref"])
        fmt.require(value[stream + "Ref"]["mediaType"] == "application/octet-stream" and value[stream + "Ref"]["encoding"] == "raw")
        facts = value[stream + "Facts"]
        fmt.fields(facts, ("observedBytes", "eof", "limitExceeded", "readFailure", "decoding"))
        cap = req[stream + "Bytes"]
        fmt.integer(facts["observedBytes"], len(raw), cap + 1)
        fmt.require(len(raw) <= cap and all(type(facts[k]) is bool for k in ("eof", "limitExceeded", "readFailure")))
        fmt.require(not facts["limitExceeded"] or facts["observedBytes"] == cap + 1 and len(raw) == cap)
        fmt.require(not facts["eof"] or facts["observedBytes"] == len(raw))
        try:
            decoded[stream] = raw.decode("utf-8", errors="strict")
            fmt.require(facts["decoding"] == "utf8")
        except UnicodeError:
            decoded[stream] = raw.decode("utf-8", errors="replace")
            fmt.require(facts["decoding"] == "utf8-replaced")
        complete &= facts["eof"] and not facts["limitExceeded"] and not facts["readFailure"]
    if value["status"] == "completed":
        fmt.require(value["phase"] == "completed" and value["launchState"] == "started" and value["exitCode"] is not None
            and value["stopReason"] is None and value["failureCategory"] is None and complete
            and cleanup["disposition"] == "confirmed")
    if value["status"] == "cancelled":
        fmt.require(value["stopReason"] == "cancelled")
    if value["status"] == "timeout":
        fmt.require(value["stopReason"] == "timeout")
    if value["status"] == "output-limit":
        fmt.require(value["stopReason"] in ("stdout-limit", "stderr-limit"))
        fmt.require(value[value["stopReason"].split("-")[0] + "Facts"]["limitExceeded"])
    if value["status"] == "failed":
        fmt.require(value["failureCategory"] is not None or value["stopReason"] in ("execution-failed", "interrupted"))
    if value["launchState"] == "not-attempted":
        fmt.require(value["exitCode"] is None and value["phase"] in ("admission", "ack", "cleanup"))
    return pf.schema.canonical(dict(status=value["status"], exitCode=value["exitCode"], **decoded))


def revision(ref, step, unit, grammar, blob, document):
    """Validate an exact observed source revision and its immutable predecessor link."""
    value = document(ref, "file-revision@1", REVISION)
    previous = unit["revisions"].get(step["artifact"])
    fmt.require(value["artifactId"] == step["artifact"] and value["relativePath"] == grammar.procArtifacts[step["artifact"]]
        and value["originOpId"] == step["id"] and value["previousRevisionRef"] == (None if previous is None else previous[0])
        and value["revision"] == (0 if previous is None else previous[1]["revision"] + 1))
    fmt.integer(value["revision"])
    raw = rawText(value["contentRef"], blob, grammar.procPolicy["procedureLimits"]["artifactBytes"])
    observed = value["workspaceObservation"]
    fmt.fields(observed, ("schemaId", "relativePath", "bytes", "sha256", "observedTimeNs", "identity"))
    fmt.require(observed["schemaId"] == "actant.workspace.read@1" and observed["relativePath"] == value["relativePath"]
        and observed["bytes"] == len(raw) and observed["sha256"] == fmt.digest(raw))
    fmt.integer(observed["observedTimeNs"])
    fmt.fields(observed["identity"], ("device", "inode", "size", "mtimeNs", "ctimeNs"))
    for item in observed["identity"].values():
        fmt.integer(item, 0, 2 ** 128 - 1)
    fmt.require(observed["identity"]["size"] == len(raw))
    return value, raw


def stepRequest(grammar, unit, step, ref, blob, document):
    """Validate resolved step sources against the frozen plan and successful prefix."""
    op = step["op"]
    keys = {"message": ("parts", "messageRef"), "inference": ("queryRef", "requestedOptionsRef", "attemptId"),
            "save": ("inferenceOpId", "answerRef", "extract", "artifactId", "relativePath"),
            "cli": ("commandId", "artifactId", "preRevisionRef", "commandPolicyRef")}
    value = document(ref, "step-request@1", keys[op])
    if op == "message":
        fmt.require(type(value["parts"]) is list and len(value["parts"]) == len(step["parts"]))
        parts = []
        for index, (source, declared) in enumerate(zip(step["parts"], value["parts"], strict=True)):
            if "literal" in source:
                fmt.fields(declared, ("kind", "partIndex", "textRef"))
                fmt.require(declared["kind"] == "literal")
                raw = rawText(declared["textRef"], blob, 262144)
                fmt.require(raw == source["literal"].encode("utf-8"))
            elif "input" in source:
                fmt.fields(declared, ("kind", "partIndex", "inputId", "rawRef"))
                fmt.require(declared["kind"] == "input" and declared["inputId"] == source["input"]
                    and declared["rawRef"] == grammar.procInputRefs[source["input"]])
                raw = rawText(declared["rawRef"], blob, 262144)
            elif "artifact" in source:
                fmt.fields(declared, ("kind", "partIndex", "artifactId", "revision", "revisionRef"))
                previous = unit["revisions"][source["artifact"]]
                fmt.require(declared["kind"] == "artifact" and declared["artifactId"] == source["artifact"]
                    and declared["revisionRef"] == previous[0] and declared["revision"] == previous[1]["revision"])
                saved = document(declared["revisionRef"], "file-revision@1", REVISION)
                raw = rawText(saved["contentRef"], blob, 262144)
            else:
                fmt.fields(declared, ("kind", "partIndex", "opId", "outcomeRef", "renderedRef"))
                previous = unit["cli"][source["cli"]]
                fmt.require(declared["kind"] == "cli" and declared["opId"] == source["cli"]
                    and declared["outcomeRef"] == previous[0])
                document(declared["outcomeRef"], "step-outcome@1", OUTCOME)
                raw = rawText(declared["renderedRef"], blob, 1048576)
                fmt.require(raw == previous[1])
            fmt.require(type(declared["partIndex"]) is int and declared["partIndex"] == index)
            parts.append(raw)
        message = document(value["messageRef"], "message@1", ("role", "origin", "contentRef"))
        raw = rawText(message["contentRef"], blob, grammar.procPolicy["procedureLimits"]["messageBytes"])
        fmt.require(raw == b"".join(parts) and message["role"] == step["role"] and message["origin"] == "authored")
        value["resolvedMessage"] = dict(role=message["role"], content=raw.decode("utf-8"))
    elif op == "inference":
        query = fmt.decode(blob(value["queryRef"]))
        fmt.fields(query, ("formatId", "messages"))
        fmt.require(query == dict(formatId="actant.llm.messages@1", messages=unit["history"])
            and query["messages"] and query["messages"][-1]["role"] == "user" and len(query["messages"]) <= 80)
        fmt.require(fmt.decode(blob(value["requestedOptionsRef"])) == unit["config"]["defaults"] | step.get("overrides", {}))
        planned = grammar.planned[grammar.activeUnit]
        fmt.require(value["attemptId"] == next(a["attemptId"] for a in planned["attemptIds"] if a["turnId"] == step["id"]))
    elif op == "save":
        fmt.require(value["inferenceOpId"] == step["inference"] and value["extract"] == step["extract"]
            and value["artifactId"] == step["artifact"] and value["relativePath"] == step["path"])
        fmt.require(rawText(value["answerRef"], blob, 262144).decode("utf-8") == unit["answers"][step["inference"]])
    else:
        fmt.require(value["commandId"] == step["command"] and value["artifactId"] == step["artifact"]
            and value["preRevisionRef"] == unit["revisions"][step["artifact"]][0])
        document(value["preRevisionRef"], "file-revision@1", REVISION)
        fmt.require(fmt.decode(blob(value["commandPolicyRef"])) == grammar.procPolicy["approvedCommands"][step["command"]])
    return value


def stepOutcome(grammar, unit, step, ref, blob, document):
    """Accept valid failure evidence while refusing full success without every receipt."""
    value = document(ref, "step-outcome@1", OUTCOME)
    fmt.require(value["status"] in ("succeeded", "failed", "cancelled"))
    fmt.require(value["reason"] in ("completed", "execution-failed", "capture-failed", "post-file-unavailable",
        "unexpected-exit", "cancelled", "timeout", "output-limit", "unknown-cleanup", "archive-failed", "validation-failed"))
    for name in ("startedTimeNs", "endedTimeNs", "durationNs"):
        fmt.integer(value[name])
    success = value["status"] == "succeeded"
    fmt.require((value["error"] is None) == success and (value["reason"] == "completed") == success)
    if not success:
        fmt.fields(value["error"], ("category", "stage"))
        fmt.require(value["error"]["category"] == value["reason"] and value["error"]["stage"] in
            ("message", "inference", "save", "cli-prepare", "cli-ack", "cli-execute", "post-file", "archive"))
    intent = unit["current"]["value"]
    if value["outputRef"] is None:
        fmt.require(not success)
        return value
    op = step["op"]
    keys = {"message": ("messageRef",), "inference": ("turnResultRef",),
            "save": ("answerRef", "extractedRef", "revisionRef"),
            "cli": ("processRequestRef", "processOutcomeRef", "postRevisionRef", "postCapture")}
    output = document(value["outputRef"], op + "-output@1", keys[op])
    if op == "message":
        fmt.require(output["messageRef"] == intent["messageRef"])
        document(output["messageRef"], "message@1", ("role", "origin", "contentRef"))
        if success:
            unit["history"].append(intent["resolvedMessage"])
    elif op == "inference":
        turn = grammar.turns[grammar.activeUnit, step["id"]]
        fmt.require(output["turnResultRef"] == turn.get("resultRef"))
        result = document(output["turnResultRef"], "turn-result@2", ("result",))["result"]
        query = fmt.decode(blob(intent["queryRef"]))
        fmt.require(result["query"] == query and result["input"] == query["messages"][-1]["content"]
            and result["requestedOptions"] == fmt.decode(blob(intent["requestedOptionsRef"])))
        if success:
            fmt.require(result["status"] == "succeeded" and result["cleanup"]["quiescence"] == "confirmed"
                        and result["error"] is None and result["partial"] is None)
            unit["history"].append(dict(role="assistant", content=result["answer"]))
            unit["answers"][step["id"]] = result["answer"]
    elif op == "save":
        fmt.require(output["answerRef"] == intent["answerRef"])
        answer = rawText(output["answerRef"], blob, 262144).decode("utf-8")
        extracted = rawText(output["extractedRef"], blob, grammar.procPolicy["procedureLimits"]["artifactBytes"])
        fmt.require(extracted == pf.extract(answer, step["extract"], grammar.procPolicy["procedureLimits"]["artifactBytes"]))
        if output["revisionRef"] is not None:
            saved, raw = revision(output["revisionRef"], step, unit, grammar, blob, document)
            fmt.require(saved["contentRef"] == output["extractedRef"] and raw == extracted)
            unit["revisions"][step["artifact"]] = output["revisionRef"], saved
        fmt.require(not success or output["revisionRef"] is not None)
    else:
        observed = None
        if output["processRequestRef"] is not None:
            req = fmt.decode(blob(output["processRequestRef"]))
            request(req, step, unit, grammar)
            if unit["current"].get("processRequest") is not None:
                fmt.require(output["processRequestRef"] == unit["current"]["processRequest"])
        else:
            fmt.require(output["processOutcomeRef"] is None and unit["current"].get("processRequest") is None)
        if output["processOutcomeRef"] is not None:
            observed = document(output["processOutcomeRef"], "process-outcome@1", PROCESS_OUTCOME)
            rendered = process(observed, (output["processRequestRef"], req), blob)
            fmt.require(observed["launchState"] == "not-attempted" or unit["current"].get("processRequest") is not None)
            unit["processBoundary"] = "unknown" if observed["cleanup"]["disposition"] == "unknown" else "confirmed"
        post = output["postCapture"]
        fmt.fields(post, ("status", "reason"))
        fmt.require(post["status"] in ("available", "unavailable") and post["reason"] in
            ("observed", "not-started", "missing", "invalid-utf8", "over-limit", "changed", "read-failed", "cleanup-unknown"))
        fmt.require((output["postRevisionRef"] is not None) == (post["status"] == "available") == (post["reason"] == "observed"))
        previous = unit["revisions"][step["artifact"]]
        saved = None
        if output["postRevisionRef"] is not None:
            fmt.require(observed is not None and observed["launchState"] != "not-attempted" and observed["cleanup"]["disposition"] == "confirmed")
            saved, raw = revision(output["postRevisionRef"], step, unit, grammar, blob, document)
            unit["revisions"][step["artifact"]] = output["postRevisionRef"], saved
        if observed is not None:
            if observed["cleanup"]["disposition"] == "unknown":
                fmt.require(post == dict(status="unavailable", reason="cleanup-unknown") and not success)
            elif observed["launchState"] == "not-attempted":
                fmt.require(post == dict(status="unavailable", reason="not-started") and not success)
            else:
                fmt.require(post["reason"] != "not-started")
        if success:
            command = grammar.procPolicy["approvedCommands"][step["command"]]
            fmt.require(observed is not None and observed["status"] == "completed" and observed["exitCode"] in command["allowedExitCodes"]
                and saved is not None and (command["mutates"] or saved["contentRef"] == previous[1]["contentRef"]))
            unit["cli"][step["id"]] = ref, rendered
    return value


def apply(grammar, data, blob, document):
    """Apply archive3 additions while retaining original transport/partial/PM checks."""
    kind, payload, unitId, turnId = data["kind"], data["payload"], data["unitId"], data["turnId"]
    if kind == "run-inputs":
        fmt.require(not grammar.started and not hasattr(grammar, "procDefinition") and unitId is None and turnId is None)
        original = rawText(payload["originalDefinitionRef"], blob, 1048576)
        normalized = blob(payload["normalizedDefinitionRef"])
        fmt.require(fmt.digest(normalized) == grammar.header["definitionSha256"])
        grammar.procDefinition = fmt.decode(normalized)
        fmt.require(pf.isProcedure(grammar.procDefinition) and normalized == pf.schema.canonical(grammar.procDefinition))
        grammar.procOriginalBytes = len(original)
        grammar.procInputRefs = {}
        index = document(payload["inputsIndexRef"], "inputs-index@1", ("entries",))
        fmt.require(type(index["entries"]) is list and len(index["entries"]) <= 16)
        for entry in index["entries"]:
            fmt.fields(entry, ("inputId", "sourceRelativePath", "rawRef", "observation"))
            fmt.require(entry["inputId"] in grammar.procDefinition["inputs"] and entry["inputId"] not in grammar.procInputRefs
                and entry["sourceRelativePath"] == grammar.procDefinition["inputs"][entry["inputId"]])
            rawText(entry["rawRef"], blob, 262144)
            observed = entry["observation"]
            fmt.fields(observed, ("observedTimeNs", "device", "inode", "size", "mtimeNs", "ctimeNs", "sha256"))
            for key in ("observedTimeNs", "device", "inode", "size", "mtimeNs", "ctimeNs"):
                fmt.integer(observed[key], 0, 2 ** 128 - 1)
            fmt.require(observed["size"] == entry["rawRef"]["bytes"] and observed["sha256"] == entry["rawRef"]["sha256"])
            grammar.procInputRefs[entry["inputId"]] = entry["rawRef"]
        fmt.require(list(grammar.procInputRefs) == sorted(grammar.procDefinition["inputs"]))
        grammar.procInputsSha = pf.semanticInputs(index["entries"])
        grammar.procInterpreter = document(payload["interpreterPolicyRef"], "interpreter-policy@1",
                                           ("definitionFormat", "parser", "normalization", "limits", "sourceIdentity"))
        fmt.require(grammar.procInterpreter["definitionFormat"] in ("json", "json5")
            and grammar.procInterpreter["normalization"] == "sorted-compact-utf8-json")
        fmt.fields(grammar.procInterpreter["parser"], ("name", "version"))
        fmt.require(grammar.procInterpreter["parser"]["name"] in ("json5", "stdlib-json"))
        pf.text(grammar.procInterpreter["parser"]["version"], 64)
        fmt.require(type(grammar.procInterpreter["sourceIdentity"]) is list and len(grammar.procInterpreter["sourceIdentity"]) <= 64)
        for item in grammar.procInterpreter["sourceIdentity"]:
            fmt.fields(item, ("path", "sha256"))
            pf.text(item["path"], 1024)
            fmt.hashValue(item["sha256"])
        return True
    if kind == "suite-planned":
        fmt.require(hasattr(grammar, "procDefinition") and not grammar.started and unitId is None and turnId is None)
        fmt.require(fmt.decode(blob(payload["definitionRef"])) == grammar.procDefinition)
        provenance = document(payload["initialProvenanceRef"], "provenance@2", ("facts",))["facts"]
        fmt.fields(provenance, ("platformBaseline", "sources", "assets", "capturePolicy", "serverConsumption",
            "providerEffectiveState", "validationPolicy", "toolPolicyRef"))
        fmt.require(provenance["capturePolicy"] in ("required", "disabled") and provenance["serverConsumption"] == "unknown")
        fmt.require(type(provenance["platformBaseline"]) is str and re.fullmatch("[a-f0-9]{40}", provenance["platformBaseline"]) is not None
            and provenance["providerEffectiveState"] == "unknown-unqueried")
        fmt.require(type(provenance["sources"]) is list and len(provenance["sources"]) <= 64)
        for source in provenance["sources"]:
            fmt.fields(source, ("path", "sha256", "versionHeader"))
            pf.text(source["path"], 1024)
            pf.text(source["versionHeader"], 2048)
            fmt.hashValue(source["sha256"])
        fmt.require(grammar.procInterpreter["sourceIdentity"] == [{k: s[k] for k in ("path", "sha256")}
            for s in provenance["sources"] if s["path"].startswith("first-party/applications/experimentRunner/")])
        fmt.require(type(provenance["assets"]) is list and len(provenance["assets"]) <= 128)
        for asset in provenance["assets"]:
            fmt.fields(asset, ("path", "role", "sha256", "status", "observedTimeNs", *(("bytes",) if "bytes" in asset else ())))
            pf.text(asset["path"], 32768)
            fmt.require(asset["role"] in ("executable", "model") and asset["status"] in
                ("unavailable-not-selected", "hash-observed", "unavailable-changed", "unavailable"))
            fmt.integer(asset["observedTimeNs"])
            fmt.require((asset["status"] == "hash-observed") == (asset["sha256"] is not None) == ("bytes" in asset))
            if asset["sha256"] is not None:
                fmt.hashValue(asset["sha256"])
                fmt.integer(asset["bytes"])
        grammar.captureRequired = provenance["capturePolicy"] == "required"
        grammar.procPolicy = provenance["validationPolicy"]
        fmt.require(pf.validate(grammar.procDefinition, grammar.procPolicy).data == pf.schema.canonical(grammar.procDefinition)
            and grammar.procInterpreter["limits"] == grammar.procPolicy["procedureLimits"])
        grammar.procTools = document(provenance["toolPolicyRef"], "tool-policy@1", ("commands", "tools"))
        fmt.require(grammar.procTools["commands"] == grammar.procPolicy["approvedCommands"])
        expectedTools = {c["tool"] for c in grammar.procTools["commands"].values()}
        fmt.require(type(grammar.procTools["tools"]) is list and len(grammar.procTools["tools"]) == len(expectedTools))
        names = set()
        for tool in grammar.procTools["tools"]:
            fmt.fields(tool, ("toolName", "configuredExecutable", "observedExecutable", "sha256", "version", "availability"))
            fmt.require(tool["toolName"] in expectedTools and tool["toolName"] not in names and tool["availability"] == "observed")
            names.add(tool["toolName"])
            for key in ("configuredExecutable", "observedExecutable"):
                pf.text(tool[key], 32768)
            if tool["sha256"] is not None:
                fmt.hashValue(tool["sha256"])
            fmt.require(tool["version"] is None)
        pf.host(dict(procedureLimits=grammar.procPolicy["procedureLimits"],
            processTools={t["toolName"]: t["configuredExecutable"] for t in grammar.procTools["tools"]},
            approvedCommands=grammar.procTools["commands"], archive=grammar.caps))
        observationFormat = importlib.import_module("first-party.applications.experimentRunner.observationFormat")
        grammar.observationPolicy = fmt.decode(blob(payload["observationPolicyRef"]))
        observationFormat.policy(grammar.observationPolicy)
        fmt.require(grammar.procPolicy["observationCalls"] == (len(grammar.observationPolicy["calls"])
                    if grammar.observationPolicy["enabled"] else 0))
        pf.budget(pf.schema.Snapshot(pf.schema.canonical(grammar.procDefinition)), grammar.procPolicy, grammar.caps,
            {k: v["bytes"] for k, v in grammar.procInputRefs.items()}, originalBytes=grammar.procOriginalBytes,
            observation=grammar.observationPolicy["enabled"])
        plan = document(payload["planRef"], "procedure-plan@1", ("procedureSha256", "units"))
        fmt.require(plan["procedureSha256"] == fmt.digest(fmt.canonical(grammar.procDefinition["procedure"])))
        grammar.procArtifacts = {s["artifact"]: s["path"] for s in grammar.procDefinition["procedure"] if s["op"] == "save"}
        expected = [(i + 1, n, c["profile"]) for i, c in enumerate(grammar.procDefinition["configurations"])
                    for n in range(1, grammar.procDefinition["repetitions"] + 1)]
        fmt.require(type(plan["units"]) is list and len(plan["units"]) == len(expected))
        attempts = set()
        for unit, triple in zip(plan["units"], expected, strict=True):
            fmt.fields(unit, ("unitId", "configurationIndex", "repetition", "profileAlias", "stepIds", "turnIds", "attemptIds"))
            fmt.identifier(unit["unitId"], opaque=True)
            fmt.require(unit["unitId"] not in grammar.planned and tuple(unit[k] for k in ("configurationIndex", "repetition", "profileAlias")) == triple)
            fmt.require(unit["stepIds"] == [s["id"] for s in grammar.procDefinition["procedure"]]
                and unit["turnIds"] == [s["id"] for s in grammar.procDefinition["procedure"] if s["op"] == "inference"]
                and type(unit["attemptIds"]) is list and len(unit["attemptIds"]) == len(unit["turnIds"]))
            for turn, attempt in zip(unit["turnIds"], unit["attemptIds"], strict=True):
                fmt.fields(attempt, ("turnId", "attemptId"))
                fmt.identifier(attempt["attemptId"], opaque=True)
                fmt.require(attempt["turnId"] == turn and attempt["attemptId"] not in attempts)
                attempts.add(attempt["attemptId"])
            grammar.planned[unit["unitId"]] = unit
        for selected in grammar.observationPolicy["calls"]:
            fmt.require(any(selected == dict(configurationIndex=u["configurationIndex"], repetition=u["repetition"], turnId=t)
                for u in grammar.planned.values() for t in u["turnIds"]))
        grammar.procProvenance = payload["initialProvenanceRef"]
        grammar.started = True
        return True
    fmt.require(grammar.started)
    if kind == "unit-planned":
        fmt.require(turnId is None and grammar.activeUnit is None and unitId not in grammar.units
            and list(grammar.planned)[grammar.nextUnit] == unitId)
        fmt.require(not grammar.unitStatuses or list(grammar.unitStatuses.values())[-1] == "succeeded")
        planned = grammar.planned[unitId]
        fmt.require(all(payload[k] == planned[k] for k in ("configurationIndex", "repetition", "profileAlias")))
        fmt.identifier(payload["jobId"])
        config = grammar.procDefinition["configurations"][planned["configurationIndex"] - 1]
        fmt.require(fmt.decode(blob(payload["requestedOptionsRef"])) == config and payload["provenanceRef"] == grammar.procProvenance)
        document(payload["provenanceRef"], "provenance@2", ("facts",))
        grammar.units[unitId] = dict(turnIndex=0, active=None, jobId=payload["jobId"], config=config,
            index=0, current=None, history=[], answers={}, revisions={}, cli={}, results=[], intents=[],
            processIdentity=None, processBoundary="not-used", failed=False)
        grammar.activeUnit, grammar.nextUnit = unitId, grammar.nextUnit + 1
        return True
    if kind in ("step-planned", "step-completed", "process-admitted", "turn-planned"):
        fmt.require(unitId == grammar.activeUnit and unitId in grammar.units and grammar.suiteOutcome is None)
        unit = grammar.units[unitId]
        step = grammar.procDefinition["procedure"][unit["index"]]
        if kind == "step-planned":
            fmt.require(turnId is None and unit["current"] is None and not unit["failed"]
                and payload["opId"] == step["id"] and payload["op"] == step["op"]
                and type(payload["ordinal"]) is int and payload["ordinal"] == unit["index"] + 1)
            value = stepRequest(grammar, unit, step, payload["requestRef"], blob, document)
            unit["current"] = dict(ref=payload["requestRef"], value=value, processRequest=None)
            unit["intents"].append(step["id"])
        elif kind == "process-admitted":
            fmt.require(turnId is None and step["op"] == "cli" and payload["opId"] == step["id"]
                and unit["current"] is not None and unit["current"]["processRequest"] is None)
            value = fmt.decode(blob(payload["processRequestRef"]))
            request(value, step, unit, grammar)
            unit["current"]["processRequest"] = payload["processRequestRef"]
            unit["processBoundary"] = "unknown"
        elif kind == "turn-planned":
            fmt.require(step["op"] == "inference" and turnId == step["id"] and unit["current"] is not None
                and unit["active"] is None and (unitId, turnId) not in grammar.turns)
            intent = unit["current"]["value"]
            fmt.require(payload == {k: intent[k] for k in ("queryRef", "requestedOptionsRef", "attemptId")})
            blob(payload["queryRef"])
            blob(payload["requestedOptionsRef"])
            grammar.turns[unitId, turnId] = dict(attemptId=payload["attemptId"], phase=None,
                answerOffset=0, reasoningOffset=0, eventTo=0, partials=0, providerCallId=None,
                serializedRef=None, attempted=False, headersObserved=False, finalFacts=None)
            unit["active"], unit["turnIndex"] = turnId, unit["turnIndex"] + 1
        else:
            fmt.require(turnId is None and unit["current"] is not None and payload["opId"] == step["id"])
            value = stepOutcome(grammar, unit, step, payload["outcomeRef"], blob, document)
            unit["results"].append(dict(opId=step["id"], outcomeRef=payload["outcomeRef"], status=value["status"], reason=value["reason"]))
            unit["failed"] = value["status"] != "succeeded"
            unit["current"], unit["index"] = None, unit["index"] + 1
        return True
    if kind == "unit-outcome":
        fmt.require(unitId == grammar.activeUnit and turnId is None)
        unit = grammar.units[unitId]
        value = document(payload["resultRef"], "unit-summary@2", ("unitId", "status", "jobOutcome", "lifetimeBoundary",
            "reservation", "configurationEvidence", "turnResults", "stepResults", "pendingStep", "unstarted", "processBoundary", "workspaceDisposition"))
        fmt.require(value["unitId"] == unitId and value["status"] in ("succeeded", "failed", "cancelled")
            and value["jobOutcome"] in (None, "Pending", "Running", "Succeeded", "Failed", "Cancelled")
            and value["lifetimeBoundary"] in ("not-started", "completed", "failed-or-unknown")
            and value["processBoundary"] == unit["processBoundary"]
            and value["workspaceDisposition"] in ("ephemeral-ended", "retained", "unknown"))
        fmt.fields(value["reservation"], ("acquisition", "close", "releaseReceipt"))
        fmt.require(value["reservation"]["acquisition"] in ("not-attempted", "attempted", "acquired", "failed")
            and value["reservation"]["close"] in ("not-attempted", "queued", "failed")
            and value["reservation"]["releaseReceipt"] == "unavailable"
            and (value["configurationEvidence"] is None or type(value["configurationEvidence"]) is dict))
        fmt.require(value["stepResults"] == [{k: r[k] for k in ("opId", "outcomeRef")} for r in unit["results"]]
            and value["pendingStep"] == (None if unit["current"] is None else grammar.procDefinition["procedure"][unit["index"]]["id"])
            and value["unstarted"] == [s["id"] for s in grammar.procDefinition["procedure"] if s["id"] not in unit["intents"]]
            and value["turnResults"] == [t["resultRef"] for (u, tId), t in grammar.turns.items() if u == unitId and "resultRef" in t])
        if value["status"] == "succeeded":
            fmt.require(value["jobOutcome"] == "Succeeded" and value["lifetimeBoundary"] == "completed"
                and not unit["failed"] and len(unit["results"]) == len(grammar.procDefinition["procedure"])
                and value["pendingStep"] is None and not value["unstarted"] and value["workspaceDisposition"] == "ephemeral-ended"
                and value["processBoundary"] in ("confirmed", "not-used")
                and value["reservation"] == dict(acquisition="acquired", close="queued", releaseReceipt="unavailable"))
            fmt.require(all(t.get("resultStatus") == "succeeded" and t["cleanup"]["quiescence"] == "confirmed"
                            for (u, tId), t in grammar.turns.items() if u == unitId))
        grammar.terminals[unitId], grammar.unitStatuses[unitId] = payload["resultRef"], value["status"]
        grammar.unitObservations[unitId] = {k: value[k] for k in ("jobOutcome", "lifetimeBoundary", "reservation")}
        grammar.activeUnit = None
        unit["history"], unit["answers"], unit["cli"] = [], {}, {}
        return True
    if kind == "suite-outcome":
        fmt.require(unitId is None and turnId is None and grammar.activeUnit is None and grammar.suiteOutcome is None)
        value = document(payload["summaryRef"], "suite-summary@2", ("status", "unitResults", "unstarted", "startedTimeNs", "endedTimeNs", "durationNs"))
        fmt.require(value["status"] in ("succeeded", "failed", "cancelled") and value["unitResults"] == list(grammar.terminals.values())
            and value["unstarted"] == [u for u in grammar.planned if u not in grammar.units])
        for key in ("startedTimeNs", "endedTimeNs", "durationNs"):
            fmt.integer(value[key])
        if value["status"] == "succeeded":
            fmt.require(len(grammar.terminals) == len(grammar.planned) and all(x == "succeeded" for x in grammar.unitStatuses.values()))
        grammar.suiteOutcome = value["status"]
        return True
    if kind == "cleanup-observed":
        fmt.require(unitId is None and turnId is None and payload["component"] in ("signal", "host", "temporary", "process", "workspace")
            and payload["observedOutcome"] in ("completed", "failed-or-unknown", "unavailable", "retained"))
        fmt.require(type(fmt.decode(blob(payload["evidenceRef"]))) is dict)
        return True
    if kind == "archive-seal":
        fmt.require(unitId is None and turnId is None and grammar.suiteOutcome is not None
            and payload["lastObservedSuiteStatus"] == grammar.suiteOutcome and payload["finalizationObserved"] == "not-yet-closed")
        for (unit, turn), attempt in grammar.turns.items():
            planned = grammar.planned[unit]
            if dict(configurationIndex=planned["configurationIndex"], repetition=planned["repetition"], turnId=turn) in grammar.observationPolicy["calls"]:
                fmt.require(any(o["unitId"] == unit and o["turnId"] == turn and o["cleanup"] for o in grammar.observations.values()))
        fmt.integer(payload["terminalSequence"], 1, data["sequence"] - 1)
        grammar.seal = data["sequence"]
        return True
    fmt.require(kind in ("request-evidence", "partial", "turn-completed", "observation-snapshot", "observation-cleanup"))
    return False


def view(grammar, unitId=None):
    """Project planned/current/terminal steps from the validated prefix only."""
    result = []
    for ident, planned in grammar.planned.items():
        if unitId is not None and ident != unitId:
            continue
        unit = grammar.units.get(ident)
        completed = {} if unit is None else {r["opId"]: r for r in unit["results"]}
        for index, step in enumerate(grammar.procDefinition["procedure"]):
            terminal = completed.get(step["id"])
            result.append(dict(unitId=ident, opId=step["id"], ordinal=index + 1, op=step["op"],
                status=terminal["status"] if terminal else "pending" if unit is not None and step["id"] in unit["intents"] else "not-started",
                reason=terminal["reason"] if terminal else "pending" if unit is not None and step["id"] in unit["intents"] else "not-started",
                outcomeRef=None if terminal is None else terminal["outcomeRef"]))
    return result
