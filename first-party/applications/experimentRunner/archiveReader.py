# file: first-party/applications/experimentRunner/archiveReader.py ; version: 2
"""Bounded read-only committed-prefix recovery, independent of backend and assets."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import stat

fmt = importlib.import_module("first-party.applications.experimentRunner.archiveFormat")


class ViewLimit(fmt.ArchiveError):
    """Selected view exceeds its cap; the underlying archive is not called corrupt."""


def budget(size, maximum):
    """Reject an oversized selected view rather than silently truncating evidence."""
    if size > maximum:
        raise ViewLimit("Selected archive view exceeds its bound.")


def regular(path, *, directory=False):
    """Reject links/reparse points and nonregular files at each fixed path boundary."""
    info = Path(path).lstat()
    fmt.require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 1024)
    fmt.require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    return info.st_size


def readBytes(path, limit):
    """Bound actual bytes even when size changes after stat; never write or follow links."""
    fmt.require(regular(path) <= limit)
    with Path(path).open("rb") as source:
        raw = source.read(limit + 1)
    fmt.require(len(raw) <= limit)
    return raw


class BlobReader:
    """Resolve only digest-derived regular blobs within one explicit archive."""

    def __init__(self, root, caps):
        """Keep no execution/runtime/config authority; reads are private diagnostics."""
        self.root, self.caps = root, caps
        self.cache = {}
        self.readTotal = 0
        regular(root / "artifacts", directory=True)

    def __call__(self, ref):
        """Validate exact length/digest before returning bounded original bytes."""
        fmt.reference(ref, self.caps)
        if ref["sha256"] in self.cache:
            raw = self.cache[ref["sha256"]]
            fmt.require(len(raw) == ref["bytes"])
            return raw
        path = self.root / "artifacts" / (ref["sha256"] + ".blob")
        budget(self.readTotal + ref["bytes"], self.caps["archiveBytes"] * 4)
        raw = readBytes(path, self.caps["artifactBytes"])
        self.readTotal += len(raw)
        fmt.require(len(raw) == ref["bytes"] and fmt.digest(raw) == ref["sha256"])
        self.cache[ref["sha256"]] = raw
        return raw


def inspectArchive(directory, *, unitId=None, turnId=None, includeContent=False):
    """Return a validated read-only view; corruption preserves only the valid prefix.

    Missing final LF or unmatched final DATA is a torn tail. Complete malformed
    frames, bad chains or referenced blobs are corruption, never complete success.
    On-disk commits do not prove the caller observed ACK return or final close.
    """
    failure = False
    try:
        return _inspectArchive(Path(directory), unitId, turnId, includeContent)
    except Exception:
        failure = True
    if failure:
        raise fmt.ArchiveError("Archive cannot be inspected.")


def _inspectArchive(root, unitId, turnId, includeContent):
    """Perform bounded fixed-path reads without backend imports or mutation."""
    regular(root, directory=True)
    rawHeader = readBytes(root / "manifest.json", 65536)
    header = fmt.decode(rawHeader)
    fmt.require(rawHeader == fmt.canonical(header))
    fmt.manifest(header)
    caps = header["limits"]
    version = int(header["schemaId"].rsplit("@", 1)[1])
    reader, grammar = BlobReader(root, caps), fmt.Grammar(header)
    total = 0
    count = 0
    artifactNames = set()
    for base in (root, root / "artifacts"):
        with os.scandir(base) as entries:
            for item in entries:
                count += 1
                fmt.require(count <= caps["logicalRecords"] * 4 + 8)
                info = item.stat(follow_symlinks=False)
                fmt.require(not item.is_symlink() and not getattr(info, "st_file_attributes", 0) & 1024)
                if item.is_file(follow_symlinks=False):
                    if base == root / "artifacts":
                        artifactNames.add(item.name)
                    total += info.st_size
                    fmt.require(total <= caps["archiveBytes"])
    regular(root / "records.log")
    view = dict(schemaId=fmt.PREFIX + "archive-view@" + str(version), archiveId=header["archiveId"],
        suiteId=header["suiteId"], status="incomplete", tail="none", committedSequence=0,
        ackObservation="unavailable", writerCloseObservation="unavailable",
        lastObservedSuiteStatus=None, records=[], content=[], failureSequence=None)
    previous = fmt.digest(rawHeader)
    retained = 0
    pending = None
    accepted = set()
    journalBytes = 0
    with (root / "records.log").open("rb") as source:
        while True:
            line = source.readline(caps["frameBytes"] + 1)
            if not line:
                if pending is not None:
                    view["tail"] = "uncommitted-data"
                break
            journalBytes += len(line)
            budget(journalBytes, caps["archiveBytes"])
            if len(line) > caps["frameBytes"]:
                view.update(status="corrupt", failureSequence=view["committedSequence"] + 1)
                break
            if not line.endswith(b"\n"):
                view["tail"] = "torn-final-frame"
                break
            if line == b"\n" and not source.peek(1):
                view["tail"] = "empty-final-frame"
                break
            try:
                value = fmt.decode(line[:-1])
                fmt.require(line == fmt.canonical(value) + b"\n")
                if pending is None:
                    fmt.fields(value, fmt.DATA_KEYS)
                    fmt.require(value["schemaId"] == fmt.PREFIX + "archive-record@" + str(version))
                    fmt.require(type(value["kind"]) is str)
                    fmt.fields(value["payload"], fmt.payloadKeys(value["kind"], version))
                    fmt.require(value["sequence"] == view["committedSequence"] + 1)
                    fmt.integer(value["sequence"], 1, caps["logicalRecords"])
                    fmt.require(value["previousCommitHash"] == previous and value["dataSha256"] == fmt.hashWithout(value, "dataSha256"))
                    pending = value
                    continue
                fmt.require(len(line) <= 1024)
                fmt.fields(value, fmt.COMMIT_KEYS)
                fmt.integer(value["sequence"], 1, caps["logicalRecords"])
                fmt.require(value["schemaId"] == fmt.PREFIX + "archive-commit@" + str(version) and
                    value["sequence"] == pending["sequence"] and value["dataSha256"] == pending["dataSha256"] and
                    value["previousCommitHash"] == previous and value["commitSha256"] == fmt.hashWithout(value, "commitSha256"))
                grammar.apply(pending, reader)
                selected = (unitId is None or pending["unitId"] == unitId) and (turnId is None or pending["turnId"] == turnId)
                if selected:
                    retained += len(fmt.canonical(pending))
                    budget(retained, caps["inspectBytes"])
                    view["records"].append(pending)
                    if includeContent:
                        for ref in pending["artifacts"]:
                            raw = reader(ref)
                            retained += len(raw)
                            budget(retained, caps["inspectBytes"])
                            # Raw request bytes stay distinct; hex is explicit and bounded.
                            item = dict(sha256=ref["sha256"], bytes=ref["bytes"], encoding=ref["encoding"])
                            item["value"] = fmt.decode(raw) if ref["mediaType"] == "application/json" and ref["encoding"] == "utf-8" else raw.hex()
                            retained += len(fmt.canonical(item))
                            budget(retained, caps["inspectBytes"])
                            view["content"].append(item)
                previous = value["commitSha256"]
                accepted.update(ref["sha256"] for ref in pending["artifacts"])
                view["committedSequence"] = pending["sequence"]
                view["lastObservedSuiteStatus"] = grammar.suiteOutcome
                reader.cache.clear()
                pending = None
            except ViewLimit:
                raise
            except Exception:
                view.update(status="corrupt", failureSequence=view["committedSequence"] + 1)
                break
    if view["status"] != "corrupt" and grammar.seal is not None and view["tail"] == "none":
        view["status"] = "sealed"
    view["orphanArtifacts"] = len(artifactNames - {key + ".blob" for key in accepted})
    observations = []
    for key, unit in grammar.planned.items():
        if unitId is not None and key != unitId:
            continue
        observed = grammar.unitObservations.get(key)
        turns = []
        for turn in unit["turnIds"]:
            if turnId is not None and turn != turnId:
                continue
            state = grammar.turns.get((key, turn))
            turns.append(dict(turnId=turn, lastObservedStatus="not-started" if state is None else state.get("resultStatus", "planned"),
                committedAnswerCharacters=0 if state is None else state["answerOffset"],
                committedReasoningCharacters=0 if state is None else state["reasoningOffset"],
                capturePhase=None if state is None else state["phase"],
                cleanup={"status": "unavailable", "quiescence": "unknown"} if state is None else state.get("cleanup", {"status": "unavailable", "quiescence": "unknown"})))
        observations.append(dict(unitId=key, configurationIndex=unit["configurationIndex"], repetition=unit["repetition"],
            lastObservedStatus=grammar.unitStatuses.get(key, "planned" if key in grammar.units else "not-started"),
            jobOutcome="unavailable" if observed is None and key in grammar.units else None if observed is None else observed["jobOutcome"],
            lifetimeBoundary="unavailable" if observed is None else observed["lifetimeBoundary"], turns=turns))
    view["units"] = observations
    view["limits"] = dict(caps)
    if version == 2:
        view["observations"] = grammar.observations
    fmt.require(unitId is None or unitId in grammar.planned)
    fmt.require(turnId is None or any(turnId in unit["turnIds"] for key, unit in grammar.planned.items() if unitId is None or key == unitId))
    fmt.require(len(fmt.canonical(view)) <= caps["inspectBytes"])
    return view
