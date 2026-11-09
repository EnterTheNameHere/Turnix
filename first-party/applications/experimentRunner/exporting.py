# file: first-party/applications/experimentRunner/exporting.py ; version: 4
"""Bounded safe presentation and exclusive, manifest-last offline report publication."""
from __future__ import annotations

import csv
import importlib
import io
import json
import os
from pathlib import Path
import re
import sys

reporting = importlib.import_module("first-party.applications.experimentRunner.reporting")
fmt, require = reporting.fmt, reporting.require
MAX_SUMMARY = 4 * reporting.MIB
MAX_PRIVATE = 32 * reporting.MIB


def jsonBytes(value):
    """Bound incremental ASCII JSON escaping before retaining or publishing expansion."""
    limit = MAX_PRIVATE if value.get("contentPolicy") == "selected-private" else MAX_SUMMARY
    # iterencode can emit one whole string. Reserve its maximum escaped size first.
    checkStrings(value, limit)
    parts, total = [], 0
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    for part in encoder.iterencode(value):
        total += len(part)
        require(total <= limit)
        parts.append(part)
    return "".join(parts).encode("ascii")


def checkStrings(value, limit):
    """Preflight exact escaped string expansion, rejecting oversized individual fields."""
    if type(value) is str:
        total = 2
        for char in value:
            code = ord(char)
            total += 12 if code > 65535 else 6 if code > 127 or code < 32 else 2 if char in ('"', "\\") else 1
            require(total <= limit)
    elif type(value) is dict:
        for key, item in value.items():
            checkStrings(key, limit)
            checkStrings(item, limit)
    elif type(value) is list:
        for item in value:
            checkStrings(item, limit)


def safeCell(value):
    """Escape control/bidi characters and neutralize spreadsheet formula-leading cells."""
    if value is None:
        return "unavailable"
    text = str(value)
    # JSON ASCII string presentation is lossless/readable and has no terminal controls.
    escaped = json.dumps(text, ensure_ascii=True)[1:-1]
    require(len(escaped) <= 4096)
    if text and (text[0] in "=+-@" or text[0].isspace() or ord(text[0]) < 32):
        escaped = "'" + escaped
    return escaped


def csvBytes(value):
    """Export fixed summary columns only; private selected contents never enter CSV."""
    reporting.reportSchema.validate(value)
    require(value["contentPolicy"] == "summary-only" and not value["content"])
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    if value["schemaId"] in (fmt.PREFIX + "report@1", fmt.PREFIX + "report@2"):
        require(len(value["rows"]) <= 256)
        columns = ("schemaId", "manifestSha256", "committedSequence", "commitSha256", "archiveStatus", "suiteOutcome",
                   "unitId", "configurationIndex", "repetition", "turnId", "turnStatus", "jobOutcome",
                   "lifetimeBoundary", "cleanupStatus", "cleanupQuiescence", "durationNs", "completionTokens")
        writer.writerow(columns)
        source = value["sources"][0]
        for row in value["rows"]:
            cells = [fmt.PREFIX + "turn-summary-csv@1", *[source[k] for k in columns[1:6]],
                     *[row[k] for k in columns[6:13]], row["callCleanup"].get("status"), row["callCleanup"].get("quiescence"),
                     row["metrics"][0]["value"], row["metrics"][1]["value"]]
            writer.writerow([safeCell(item) for item in cells])
            require(output.tell() <= MAX_SUMMARY)
        if "procedureSteps" in value:
            writer.writerow(("schemaId", "unitId", "opId", "ordinal", "op", "status", "reason", "processStatus", "exitCode", "postCapture", "durationNs", "stdoutBytes", "stderrBytes"))
            metrics = {(r["unitId"], r["opId"]): r for r in value["cliMetrics"]}
            for row in value["procedureSteps"]:
                metric = metrics.get((row["unitId"], row["opId"]), {})
                cells = [fmt.PREFIX + "step-summary-csv@1", *[row[k] for k in ("unitId", "opId", "ordinal", "op", "status", "reason", "processStatus", "exitCode")],
                    json.dumps(row["postCapture"], sort_keys=True), *[metric.get(k) for k in ("durationNs", "stdoutBytes", "stderrBytes")]]
                writer.writerow([safeCell(item) for item in cells])
                require(output.tell() <= MAX_SUMMARY)
    else:
        require(value["schemaId"] in (fmt.PREFIX + "comparison@1", fmt.PREFIX + "comparison@2"))
        qualified = "procedureQualification" in value
        writer.writerow(("schemaId", "mode", "result", "reasons", "sourceBindings", "selection", "alignment", "difference",
                         *(("procedureQualification",) if qualified else ())))
        writer.writerow([safeCell(fmt.PREFIX + "comparison-summary-csv@1"), safeCell(value["mode"]), safeCell(value["result"]),
                         *[safeCell(json.dumps(value[k], ensure_ascii=True, sort_keys=True, separators=(",", ":")))
                           for k in ("reasons", "sources", "selection", "alignment", "difference", *(("procedureQualification",) if qualified else ())) ]])
    require(output.tell() <= MAX_SUMMARY)
    return output.getvalue().encode("utf-8")


def table(value):
    """Show qualified outcomes, selection and measurements within one escaped summary budget."""
    reporting.reportSchema.validate(value)
    if value["schemaId"] in (fmt.PREFIX + "comparison@1", fmt.PREFIX + "comparison@2"):
        # The validated comparison is already summary-only, including its qualifiers.
        return jsonBytes(value).decode("ascii")
    lines, total = [], 0
    def append(summary):
        """Reserve each escaped line and delimiter before retaining or joining the whole output."""
        nonlocal total
        raw = jsonBytes(summary)
        total += len(raw) + (1 if lines else 0)
        require(total <= MAX_SUMMARY)
        lines.append(raw.decode("ascii"))
    append(dict(sources=value["sources"], selection=value["selection"], scopeCompleteness=value["scopeCompleteness"],
                counts=value["counts"], diagnostics=value["diagnostics"], contentPolicy="summary-only"))
    for row in value["rows"]:
        # Row schemas allow only identifiers, outcome/availability facts and typed metrics.
        append(row)
    append(dict(aggregates=value["metrics"]))
    if "procedureSteps" in value:
        append(dict(procedureIdentity=value["procedureIdentity"]))
        for row in value["procedureSteps"]:
            append(row)
        for row in value["cliMetrics"]:
            append(dict(cliMetric=row, durationUnit="nanoseconds", streamUnit="retained-bytes"))
        module = importlib.import_module("first-party.applications.experimentRunner.procedureReporting")
        append(dict(cliAggregates=module.aggregates(value["cliMetrics"])))
    return "\n".join(lines)


def stage(directory, filename, raw):
    """Flush and close one exclusive temporary file before its owned-directory rename."""
    temporary = directory / (filename + ".partial")
    target = temporary.open("xb")
    try:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    finally:
        primary = sys.exception()
        try:
            target.close()
        except BaseException:
            if primary is None:
                raise
    temporary.rename(directory / filename)


def publish(value, root, name, *, kind, format, sourceDirectories):
    """Publish complete bytes last; a marker never attests caller ACK or source success."""
    reporting.reportSchema.validate(value)
    require(type(name) is str and re.fullmatch("[A-Za-z0-9_-]{1,64}", name) is not None)
    require(kind in ("report", "compare") and format in ("json", "csv"))
    require(value["schemaId"] in tuple(fmt.PREFIX + ("report@" if kind == "report" else "comparison@") + str(i) for i in (1, 2)))
    data = jsonBytes(value) if format == "json" else csvBytes(value)
    filename = "report." + format
    manifest = dict(schemaId=fmt.PREFIX + "report-export@1", kind=kind, format=format, generator=value["generator"],
                    sources=value["sources"], selection=value["selection"], contentPolicy=value["contentPolicy"],
                    data=dict(filename=filename, bytes=len(data), sha256=fmt.digest(data)), publicationState="complete",
                    scopeCompleteness=value.get("scopeCompleteness", "not-comparable" if value.get("result") == "NotComparable" else "selected-comparison"))
    marker = jsonBytes(manifest)
    directory = Path(root) / name
    require(len(sourceDirectories) == len(value["sources"]))
    for source in sourceDirectories:
        require(not directory.resolve().is_relative_to(Path(source).resolve()))
    # The explicitly chosen root must exist and be regular; never make source-derived parents.
    reporting.reader.regular(Path(root), directory=True)
    directory.mkdir()
    stage(directory, filename, data)
    stage(directory, fmt.MANIFEST_NAME, marker)
    return manifest


def exitCode(value):
    """Separate comparison inequality/unavailability from original run outcomes."""
    if value["schemaId"] in (fmt.PREFIX + "comparison@1", fmt.PREFIX + "comparison@2"):
        return {"Equal": 0, "NotEqual": 1, "NotComparable": 2}[value["result"]]
    return 2 if value["scopeCompleteness"] == "invalid-prefix" else 0
