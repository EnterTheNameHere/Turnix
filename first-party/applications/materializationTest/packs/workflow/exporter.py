# file: first-party/applications/materializationTest/packs/workflow/exporter.py ; version: 1
from __future__ import annotations

from pathlib import Path

_MEMORY_KEY = "materializationtest"
_RUN_STATE_ADDRESS = "materialization-test/run-state"
_EXPORT_FORMAT = "materialization-test.evidence@1"


def _requireMapping(value: object, name: str) -> dict[str, object]:
    """Return one plain object after validating an export boundary.

    Args:
        value: Value expected to be a dictionary.
        name: Human-readable boundary name for diagnostics.

    Returns:
        The original dictionary.

    Raises:
        TypeError: If ``value`` is not a dictionary.
    """
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object.")
    return value


def _requireString(value: object, name: str) -> str:
    """Return one non-blank string without normalizing its contents.

    Args:
        value: Candidate string value.
        name: Human-readable boundary name for diagnostics.

    Returns:
        The exact validated string.

    Raises:
        ValueError: If the value is not a non-blank string.
    """
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-blank string.")
    return value


def _processingRunIds(state: dict[str, object]) -> tuple[str, ...]:
    """Collect ProcessingRun identities referenced by authoritative workflow state.

    Materialization attempts, self-audit attempts, and questionnaire answers are
    traversed in benchmark execution order. Duplicate identities are rejected
    because one ProcessingRun must represent exactly one inference in the export.

    Args:
        state: Authoritative Materialization Test run state.

    Returns:
        Ordered ProcessingRun identity tuple.

    Raises:
        TypeError: If an evidence collection or entry has an invalid shape.
        ValueError: If a ProcessingRun identity is blank or duplicated.
    """
    result: list[str] = []
    seen: set[str] = set()
    for field in ("materializationAttempts", "selfAuditAttempts", "questionnaireAnswers"):
        entries = state.get(field, [])
        if not isinstance(entries, list):
            raise TypeError(f"Run state {field!r} must be a list.")
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise TypeError(f"Run state {field}[{index}] must be an object.")
            processingRunId = _requireString(
                entry.get("processingRunId"),
                f"Run state {field}[{index}].processingRunId",
            )
            if processingRunId in seen:
                raise ValueError(f"Duplicate ProcessingRun identity {processingRunId!r} in run state.")
            seen.add(processingRunId)
            result.append(processingRunId)
    return tuple(result)


def _loadProcessingRuns(ctx, state: dict[str, object]) -> list[dict[str, object]]:
    """Load exact shared ProcessingRun evidence referenced by workflow state.

    The LLM pipeline is the authority for exact rendered query and raw response
    evidence. Export therefore dereferences workflow ProcessingRun IDs instead of
    rebuilding prompts from benchmark inputs.

    Args:
        ctx: Active CodeEntryContext with authoritative memory access.
        state: Authoritative Materialization Test run state.

    Returns:
        Ordered detached ProcessingRun snapshots suitable for JSON export.

    Raises:
        RuntimeError: If referenced ProcessingRun evidence is absent or corrupt.
    """
    snapshots: list[dict[str, object]] = []
    for processingRunId in _processingRunIds(state):
        address = f"processing/{_MEMORY_KEY}/runs/{processingRunId}"
        snapshot = ctx.memory.load(address)
        if not isinstance(snapshot, dict):
            raise RuntimeError(f"ProcessingRun evidence {processingRunId!r} is missing or invalid.")
        if snapshot.get("processingRunId") != processingRunId:
            raise RuntimeError(f"ProcessingRun evidence identity mismatch for {processingRunId!r}.")
        snapshots.append(snapshot)
    return snapshots


def _testDefinition(ctx) -> dict[str, object]:
    """Load the exact application-owned test definition used by this configuration.

    Args:
        ctx: Active CodeEntryContext exposing configuration and managed I/O.

    Returns:
        Exact JSON-compatible prompt/test definition object.

    Raises:
        ValueError: If ``promptsFile`` is missing or blank.
        TypeError: If the prompt document is not an object.
    """
    promptsFile = _requireString(ctx.config.get("promptsFile"), "promptsFile")
    return _requireMapping(ctx.io.readJson(promptsFile), "Prompt definitions")


def _exportPath(ctx) -> Path:
    """Build the unique evidence projection path for this ApplicationRun.

    Args:
        ctx: Active CodeEntryContext carrying Application and ApplicationRun identity.

    Returns:
        Absolute or configured-root-relative managed-I/O destination path.

    Raises:
        ValueError: If ``outputDirectory`` is absent or blank.
    """
    outputDirectory = _requireString(ctx.config.get("outputDirectory"), "outputDirectory")
    return (
        Path(outputDirectory)
        / ctx.identity.applicationId
        / ctx.identity.applicationRunId
        / "evidence.json"
    )


def _export(ctx, _payload):
    """Stage a complete scoring-evidence projection from authoritative state.

    Export is intentionally a separate Job after benchmark state has been
    accepted. The authoritative Value System remains the source of truth; this
    JSON file is a portable scoring/audit projection. Exact model-facing queries
    and responses are copied from persisted ProcessingRun evidence, while source
    attempts and analyzer process snapshots come from workflow state.

    Args:
        ctx: Active invocation context with memory and managed-I/O authority.
        _payload: Reserved capability payload; ignored in v1.

    Returns:
        JSON-compatible export summary containing path and evidence counts.

    Raises:
        RuntimeError: If authoritative run or ProcessingRun evidence is missing.
        TypeError: If authoritative/export input shapes are corrupt.
        ValueError: If required configured paths or identities are invalid.
    """
    state = ctx.memory.load(_RUN_STATE_ADDRESS)
    if not isinstance(state, dict):
        raise RuntimeError("Materialization Test has no authoritative run state to export.")
    processingRuns = _loadProcessingRuns(ctx, state)
    evidence = {
        "formatId": _EXPORT_FORMAT,
        "application": {
            "applicationId": ctx.identity.applicationId,
            "applicationRunId": ctx.identity.applicationRunId,
        },
        "testDefinition": _testDefinition(ctx),
        "configuration": ctx.config,
        "workflowState": state,
        "processingRuns": processingRuns,
    }
    path = _exportPath(ctx)
    ctx.io.writeJsonAtomic(path, evidence)
    return {
        "formatId": _EXPORT_FORMAT,
        "path": str(path),
        "processingRunCount": len(processingRuns),
        "materializationOutcome": state.get("materializationOutcome"),
        "selfAuditOutcome": state.get("selfAuditOutcome"),
        "questionnaireOutcome": state.get("questionnaireOutcome"),
    }


def onLoad(ctx):
    """Register the portable evidence-export capability for this Pack load.

    Args:
        ctx: Registration-enabled CodeEntryContext supplied by Actant.
    """
    ctx.capabilities.register("materializationTest.export@1", _export)
