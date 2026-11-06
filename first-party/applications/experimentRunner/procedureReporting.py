# file: first-party/applications/experimentRunner/procedureReporting.py ; version: 1
"""Stdlib-only procedure projections and strict report2/comparison2 validation."""
import base64
import importlib

fmt = importlib.import_module("first-party.applications.experimentRunner.archiveFormat")
schema = importlib.import_module("first-party.applications.experimentRunner.reportSchema")
STEP_KEYS = ("sourceIndex", "unitId", "opId", "ordinal", "op", "status", "reason", "processStatus", "exitCode", "postCapture", "availability")
CLI_KEYS = ("sourceIndex", "unitId", "opId", "durationNs", "stdoutBytes", "stderrBytes", "availability", "reason")
REASONS = ("completed", "execution-failed", "capture-failed", "post-file-unavailable", "unexpected-exit", "cancelled", "timeout",
    "output-limit", "unknown-cleanup", "archive-failed", "validation-failed", "not-started", "pending")


def extend(value, source, unitIds, includeContent):
    """Keep CLI measurements and private artifacts outside inference metric denominators."""
    value.update(schemaId=fmt.PREFIX + "report@2", procedureSteps=[], cliMetrics=[],
                 procedureIdentity=source.view["procedureIdentity"])
    records = source.view["records"]
    intents = {(r["unitId"], r["payload"]["opId"]): r["payload"]["requestRef"]
        for r in records if r["kind"] == "step-planned"}
    private, privateSeen = [], set()
    for step in source.view["procedureSteps"]:
        unit, op = step["unitId"], step["opId"]
        if unitIds and unit not in unitIds:
            continue
        outcome = None if step["outcomeRef"] is None else source.document(step["outcomeRef"])
        outputRef = None if outcome is None else outcome["outputRef"]
        output = None if outputRef is None else source.document(outputRef)
        process = None
        if step["op"] == "cli" and output is not None and output["processOutcomeRef"] is not None:
            process = source.document(output["processOutcomeRef"])
        value["procedureSteps"].append(dict(sourceIndex=0, unitId=unit, opId=op, ordinal=step["ordinal"],
            op=step["op"], status=step["status"], reason=step["reason"],
            processStatus=None if process is None else process["status"], exitCode=None if process is None else process["exitCode"],
            postCapture=None if step["op"] != "cli" or output is None else output["postCapture"],
            availability=dict(request=(unit, op) in intents, outcome=outcome is not None, content=output is not None)))
        if step["op"] == "cli":
            value["cliMetrics"].append(dict(sourceIndex=0, unitId=unit, opId=op,
                durationNs=None if process is None else str(process["durationNs"]),
                stdoutBytes=None if process is None else str(process["stdoutRef"]["bytes"]),
                stderrBytes=None if process is None else str(process["stderrRef"]["bytes"]),
                availability="unavailable" if process is None else "available",
                reason="measured" if process is not None else step["status"] if step["status"] in ("pending", "not-started") else "capture-unavailable"))
        if includeContent:
            refs = [ref for r in records if r["unitId"] == unit and r["turnId"] is None
                    and r["payload"].get("opId") == op for ref in r["artifacts"]]
            for ref in refs:
                key = unit, op, fmt.refKey(ref)
                if key not in privateSeen:
                    privateSeen.add(key)
                    raw = source.raw(ref)
                    private.append(dict(sourceIndex=0, unitId=unit, opId=op, artifactRef=ref,
                        dataBase64=base64.b64encode(raw).decode("ascii")))
    if includeContent:
        value["privateContent"] = private


def validate(value):
    """Validate new fields, then apply unchanged original validators to the shared projection."""
    projection = dict(value)
    report = value["schemaId"] == fmt.PREFIX + "report@2"
    projection["schemaId"] = fmt.PREFIX + ("report@1" if report else "comparison@1")
    if not report:
        item = projection.pop("procedureQualification")
        fmt.fields(item, ("leftProcedureSha256", "rightProcedureSha256", "leftInputsSha256", "rightInputsSha256",
            "leftQuerySha256", "rightQuerySha256", "differencesAllowed"))
        for name, digest in item.items():
            if name != "differencesAllowed":
                schema.nullableHash(digest)
        fmt.require(type(item["differencesAllowed"]) is bool)
        alignment = value["alignment"]
        if alignment is not None:
            fmt.require(item["differencesAllowed"] == alignment["allowInputDifferences"]
                and [item["leftQuerySha256"], item["rightQuerySha256"]] == alignment["querySha256"])
    else:
        identity = projection.pop("procedureIdentity")
        fmt.fields(identity, ("procedureSha256", "inputsSha256"))
        for digest in identity.values():
            schema.nullableHash(digest)
        steps, metrics = projection.pop("procedureSteps"), projection.pop("cliMetrics")
        fmt.require(type(steps) is list and len(steps) <= 2048 and type(metrics) is list and len(metrics) <= 512)
        seen, cli = set(), set()
        for row in steps:
            fmt.fields(row, STEP_KEYS)
            fmt.require(type(row["sourceIndex"]) is int and row["sourceIndex"] == 0)
            fmt.identifier(row["unitId"], opaque=True)
            fmt.identifier(row["opId"])
            key = row["unitId"], row["opId"]
            fmt.require(key not in seen)
            seen.add(key)
            fmt.integer(row["ordinal"], 1, 64)
            schema.enum(row["op"], ("message", "inference", "save", "cli"))
            schema.enum(row["status"], ("not-started", "pending", "succeeded", "failed", "cancelled"))
            schema.enum(row["reason"], REASONS)
            fmt.require((row["reason"] == "completed") == (row["status"] == "succeeded"))
            fmt.fields(row["availability"], ("request", "outcome", "content"))
            fmt.require(all(type(b) is bool for b in row["availability"].values()))
            fmt.require(row["availability"]["request"] == (row["status"] != "not-started")
                and row["availability"]["outcome"] == (row["status"] in ("succeeded", "failed", "cancelled")))
            if row["op"] != "cli":
                fmt.require(row["processStatus"] is None and row["exitCode"] is None and row["postCapture"] is None)
            else:
                cli.add(key)
                fmt.require(row["processStatus"] is None or row["processStatus"] in ("completed", "failed", "timeout", "cancelled", "output-limit"))
                fmt.require(row["exitCode"] is None or type(row["exitCode"]) is int)
                if row["postCapture"] is not None:
                    fmt.fields(row["postCapture"], ("status", "reason"))
                    schema.enum(row["postCapture"]["status"], ("available", "unavailable"))
                    schema.enum(row["postCapture"]["reason"], ("observed", "not-started", "missing", "invalid-utf8", "over-limit", "changed", "read-failed", "cleanup-unknown"))
        seenMetrics = set()
        for row in metrics:
            fmt.fields(row, CLI_KEYS)
            key = row["unitId"], row["opId"]
            fmt.require(row["sourceIndex"] == 0 and key in cli and key not in seenMetrics)
            seenMetrics.add(key)
            schema.enum(row["availability"], ("available", "unavailable"))
            schema.enum(row["reason"], ("measured", "not-started", "pending", "capture-unavailable"))
            fmt.require((row["availability"] == "available") == (row["reason"] == "measured"))
            for name in ("durationNs", "stdoutBytes", "stderrBytes"):
                fmt.require((row[name] is not None) == (row["availability"] == "available"))
                if row[name] is not None:
                    schema.decimal(row[name])
        fmt.require(cli == seenMetrics)
        if "privateContent" in projection:
            private = projection.pop("privateContent")
            fmt.require(value["contentPolicy"] == "selected-private" and type(private) is list and len(private) <= 2048)
            total = 0
            for item in private:
                fmt.fields(item, ("sourceIndex", "unitId", "opId", "artifactRef", "dataBase64"))
                fmt.require(item["sourceIndex"] == 0 and (item["unitId"], item["opId"]) in seen)
                ref = item["artifactRef"]
                fmt.require(item["unitId"] in value["selection"]["unitIds"])
                fmt.reference(ref, dict(fmt.DEFAULTS, requestBytes=8 * 1024 * 1024))
                total += ref["bytes"]
                fmt.require(total <= 24 * 1024 * 1024)
                fmt.require(type(item["dataBase64"]) is str and len(item["dataBase64"]) == 4 * ((ref["bytes"] + 2) // 3))
                raw = base64.b64decode(item["dataBase64"], validate=True)
                fmt.require(len(raw) == ref["bytes"] and fmt.digest(raw) == ref["sha256"])
    schema.validate(projection)
    return value


def aggregates(metrics):
    """Compute CLI-only exact denominators for retained bytes and shared outcome duration."""
    rows = []
    for key, unit in (("durationNs", "nanoseconds"), ("stdoutBytes", "bytes"), ("stderrBytes", "bytes")):
        numbers = [int(row[key]) for row in metrics if row[key] is not None]
        reasons = {}
        for row in metrics:
            if row[key] is None:
                reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
        rows.append(dict(metric=key, unit=unit, contributing=len(numbers), excluded=len(metrics) - len(numbers),
            exclusionReasons=reasons, sum=str(sum(numbers)) if numbers else None,
            mean=None if not numbers else dict(numerator=str(sum(numbers)), denominator=str(len(numbers)))))
    return rows
