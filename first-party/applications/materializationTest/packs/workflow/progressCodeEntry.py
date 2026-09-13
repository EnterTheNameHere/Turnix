# file: first-party/applications/materializationTest/packs/workflow/progressCodeEntry.py ; version: 1
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_IMPLEMENTATION_NAME = "materializationTestWorkflowImplementation"
_IMPLEMENTATION_PATH = Path(__file__).with_name("codeEntry.py")
_EXPORT_FORMAT = "materialization-test.evidence@1"
_QUESTIONNAIRE_SECTION_IDS = tuple(f"{number:02d}" for number in range(1, 14))


def _loadImplementation():
    """Load the benchmark workflow implementation used by this progress adapter."""
    existing = sys.modules.get(_IMPLEMENTATION_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(_IMPLEMENTATION_NAME, _IMPLEMENTATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to construct the Materialization workflow module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_IMPLEMENTATION_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_IMPLEMENTATION_NAME, None)
        raise
    return module


_impl = _loadImplementation()
_originalRunInference = _impl._runInference
_completedProcessingRunIds: list[str] = []


def _requireString(value: object, name: str) -> str:
    """Return one non-blank exact string used by progress evidence."""
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-blank string.")
    return value


def _exportPath(ctx) -> Path:
    """Return the same evidence destination used by the terminal exporter."""
    outputDirectory = _requireString(ctx.config.get("outputDirectory"), "outputDirectory")
    return (
        Path(outputDirectory)
        / ctx.identity.applicationId
        / ctx.identity.applicationRunId
        / "evidence.json"
    )


def _testDefinition(ctx) -> dict[str, object]:
    """Load exact application-owned prompt and questionnaire source documents."""
    promptsFile = _requireString(ctx.config.get("promptsFile"), "promptsFile")
    questionnaireDirectory = _requireString(
        ctx.config.get("questionnaireDirectory"),
        "questionnaireDirectory",
    ).rstrip("/")
    prompts = ctx.io.readJson(promptsFile)
    if not isinstance(prompts, dict):
        raise TypeError("Prompt definitions must be an object.")
    sections: list[dict[str, object]] = []
    for sectionId in _QUESTIONNAIRE_SECTION_IDS:
        section = ctx.io.readJson(f"{questionnaireDirectory}/{sectionId}.json")
        if not isinstance(section, dict):
            raise TypeError(f"Questionnaire section {sectionId} must be an object.")
        sections.append(section)
    return {"prompts": prompts, "questionnaire": {"sections": sections}}


def _processingRuns(ctx) -> list[dict[str, object]]:
    """Dereference every ProcessingRun known complete at this safe observation boundary."""
    snapshots: list[dict[str, object]] = []
    for processingRunId in _completedProcessingRunIds:
        address = f"processing/materializationtest/runs/{processingRunId}"
        snapshot = ctx.memory.load(address)
        if not isinstance(snapshot, dict):
            raise RuntimeError(
                f"Completed ProcessingRun evidence {processingRunId!r} is unavailable."
            )
        snapshots.append(snapshot)
    return snapshots


def _writeProgressEvidence(ctx, *, processingRunId: str) -> None:
    """Atomically publish a non-restorable evidence projection after one completed inference."""
    if processingRunId in _completedProcessingRunIds:
        raise RuntimeError(f"Duplicate completed ProcessingRun {processingRunId!r}.")
    _completedProcessingRunIds.append(processingRunId)
    try:
        processingRuns = _processingRuns(ctx)
        evidence = {
            "formatId": _EXPORT_FORMAT,
            "runStatus": {
                "state": "in-progress",
                "restorable": False,
                "completedProcessingRunCount": len(processingRuns),
                "lastCompletedProcessingRunId": processingRunId,
            },
            "application": {
                "applicationId": ctx.identity.applicationId,
                "applicationRunId": ctx.identity.applicationRunId,
            },
            "testDefinition": _testDefinition(ctx),
            "configuration": ctx.config,
            "workflowState": None,
            "processingRuns": processingRuns,
        }
        ctx.io.writeJsonAtomic(_exportPath(ctx), evidence)
    except Exception:
        _completedProcessingRunIds.pop()
        raise


def _runInference(
    ctx,
    *,
    inputValue,
    provider,
    model,
    providerOptions,
    streamObserver,
):
    """Run one inference and publish live evidence once its ProcessingRun is complete."""
    result = _originalRunInference(
        ctx,
        inputValue=inputValue,
        provider=provider,
        model=model,
        providerOptions=providerOptions,
        streamObserver=streamObserver,
    )
    _writeProgressEvidence(ctx, processingRunId=result.processingRunId)
    return result


def onLoad(ctx):
    """Activate the normal workflow with live evidence publication at inference boundaries."""
    _completedProcessingRunIds.clear()
    _impl._runInference = _runInference
    _impl.onLoad(ctx)
