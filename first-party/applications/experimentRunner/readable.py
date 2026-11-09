# file: first-party/applications/experimentRunner/readable.py ; version: 3
"""Explicit private, chronological text export using the validated offline archive API."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import re

reporting = importlib.import_module("first-party.applications.experimentRunner.reporting")


def exportText(directory, unitId, outputRoot, name):
    """Publish selected-unit artifacts as inert UTF-8 text in a new private directory.

    Every file has a generated .txt name; archived paths never become output
    paths. Content comes only from digest-checked committed references. The
    manifest is written last; failed publication may leave an incomplete folder.
    A valid failed/incomplete archive prefix remains inspectable. No execution,
    model/tool loading, source mutation or implicit final-response save occurs.
    """
    fmt = reporting.fmt
    reporting.require(type(name) is str and re.fullmatch("[A-Za-z0-9_-]{1,64}", name) is not None)
    source = reporting.Sources().open(directory)
    reporting.require(source.view["status"] != "corrupt")
    reporting.require(unitId in {unit["unitId"] for unit in source.view["units"]})
    output = Path(outputRoot).resolve() / name
    archive = source.root.resolve()
    reporting.require(not output.is_relative_to(archive) and not archive.is_relative_to(output))
    artifacts, records, total = [], [], 0
    seen = {}
    for record in source.view["records"]:
        if record["unitId"] != unitId:
            continue
        row = dict(sequence=record["sequence"], kind=record["kind"], turnId=record["turnId"],
                   payload=record["payload"], files=[])
        for ref in record["artifacts"]:
            key = fmt.refKey(ref)
            if key not in seen:
                raw = source.raw(ref)
                total += len(raw)
                reporting.require(total <= 24 * reporting.MIB)
                try:
                    text = raw.decode("utf-8", errors="strict")
                    encoding = "utf-8"
                except UnicodeError:
                    text, encoding = raw.hex(), "hex"
                if ref["mediaType"] == "application/json" and encoding == "utf-8":
                    text = json.dumps(fmt.decode(raw), ensure_ascii=True, indent=2)
                filename = f"{len(artifacts) + 1:04d}.txt"
                seen[key] = filename
                artifacts.append((filename, text, ref, encoding))
            row["files"].append(seen[key])
        if record["kind"] == "turn-completed":
            selected = source.selected(unitId, record["turnId"])
            result = selected["result"]
            if result is not None:
                for field in ("answer", "reasoning"):
                    text = result[field]
                    if type(text) is str:
                        total += len(text.encode("utf-8"))
                        reporting.require(total <= 24 * reporting.MIB)
                        filename = f"{len(artifacts) + 1:04d}-{field}.txt"
                        artifacts.append((filename, text, record["payload"]["resultRef"], field + "-utf8"))
                        row["files"].append(filename)
        records.append(row)
    # All source reads and validation precede output creation.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    index = ["PRIVATE ARCHIVE CONTENT: open text files in an editor; do not execute generated code.",
             "Final inference answers are separate from saved/post-tool source; no correctness claim.",
             json.dumps(source.binding, sort_keys=True), ""]
    for row in records:
        label = row["payload"].get("opId") or row["turnId"] or "unit"
        index.append(f"{row['sequence']:04d} {row['kind']} {label}: {', '.join(row['files'])}")
    for filename, text, ref, encoding in artifacts:
        with (output / filename).open("x", encoding="utf-8", newline="\n") as destination:
            destination.write(text)
    (output / "index.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    manifest = dict(schemaId="actant.experiment.text-export@1", source=source.binding, unitId=unitId,
                    contentPolicy="selected-private", records=records,
                    artifacts=[dict(file=filename, artifactRef=ref, rendering=encoding)
                               for filename, text, ref, encoding in artifacts])
    (output / fmt.MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return dict(path=str(output), files=len(artifacts), source=source.binding)
