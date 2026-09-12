# file: first-party/applications/materializationTest/packs/workflow/codeEntry.py ; version: 1
from __future__ import annotations

from collections.abc import Mapping

from backend.llm.llmTypes import LlmQuery
from backend.processing.runtime import QueryItem

_MEMORY_KEY = "materializationtest"
_RUN_STATE_ADDRESS = "materialization-test/run-state"


def _requireMapping(value: object, name: str) -> Mapping[str, object]:
    """Returns value as a mapping or raises a boundary-focused error."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object.")
    return value


def _requireString(value: object, name: str) -> str:
    """Returns a non-blank string or raises a boundary-focused error."""
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-blank string.")
    return value


def _promptDefinitions(ctx) -> dict[str, str]:
    """Loads the deliberately simple v1 prompt-definition document."""
    promptPath = _requireString(ctx.config.get("promptsFile"), "promptsFile")
    raw = ctx.io.readJson(promptPath)
    definitions = _requireMapping(raw, "Prompt definitions")
    required = (
        "grounding",
        "initialMaterialization",
        "staticAnalysisReport",
        "searchForBugsAndFix",
        "questionnaire",
    )
    result: dict[str, str] = {}
    for key in required:
        result[key] = _requireString(definitions.get(key), f"Prompt definition {key!r}")
    return result


def _llmConfig(ctx) -> tuple[str, str | None, Mapping[str, object]]:
    """Returns the provider, model and provider options for this benchmark run."""
    llm = _requireMapping(ctx.config.get("llm"), "llm")
    provider = _requireString(llm.get("provider"), "llm.provider")
    model = llm.get("model")
    if model is not None and type(model) is not str:
        raise TypeError("llm.model must be null or a string.")
    options = llm.get("providerOptions", {})
    return provider, model, _requireMapping(options, "llm.providerOptions")


def _buildQueryItems(_ctx, payload):
    """Builds phase-specific QueryItems from authoritative workflow input."""
    request = _requireMapping(payload, "BUILD_QUERY_ITEMS payload")
    inputValue = _requireMapping(request.get("input"), "BUILD_QUERY_ITEMS input")
    phase = _requireString(inputValue.get("phase"), "phase")
    if phase != "initial-materialization":
        raise ValueError(f"Unsupported materialization phase: {phase!r}.")

    grounding = _requireString(inputValue.get("grounding"), "grounding")
    instructions = _requireString(
        inputValue.get("initialMaterialization"),
        "initialMaterialization",
    )
    return [
        QueryItem(
            itemId="materialization-test:grounding",
            kind="grounding",
            content=grounding,
            metadata={"phase": phase, "role": "grounding"},
        ),
        QueryItem(
            itemId="materialization-test:initial-materialization",
            kind="instruction",
            content=instructions,
            metadata={"phase": phase, "role": "instruction"},
        ),
    ]


def _buildQuery(_ctx, payload):
    """Renders accepted QueryItems into the exact v1 text/plain model input."""
    request = _requireMapping(payload, "BUILD_QUERY payload")
    snapshots = request.get("queryItems")
    if not isinstance(snapshots, list):
        raise TypeError("BUILD_QUERY queryItems must be a list.")
    items = [QueryItem.fromSnapshot(snapshot) for snapshot in snapshots]
    return LlmQuery(
        formatId="text/plain",
        payload="\n\n".join(item.content for item in items),
        metadata={
            "application": "materializationTest",
            "phase": "initial-materialization",
            "strategy": "actant-native",
        },
    )


def _run(ctx, payload):
    """Runs the first materialization phase and commits its exact response as workflow state."""
    request = {} if payload is None else _requireMapping(payload, "Materialization run request")
    strategy = _requireString(ctx.config.get("strategy"), "strategy")
    if strategy not in {"classic", "actant-native"}:
        raise ValueError("strategy must be 'classic' or 'actant-native'.")
    if strategy != "actant-native":
        raise NotImplementedError("Classic strategy is scaffolded but not materialized yet.")

    prompts = _promptDefinitions(ctx)
    provider, model, providerOptions = _llmConfig(ctx)
    result = ctx.llm.runProcessing(
        memoryKey=_MEMORY_KEY,
        inputValue={
            "phase": "initial-materialization",
            "grounding": prompts["grounding"],
            "initialMaterialization": prompts["initialMaterialization"],
        },
        buildQueryItemsCapabilityId="materializationTest.buildQueryItems@1",
        buildQueryCapabilityId="materializationTest.buildQuery@1",
        providerName=provider,
        model=model,
        providerOptions=providerOptions,
        streamObserver=request.get("streamObserver"),
    )

    transaction = ctx.memory.openTransaction()
    transaction.set(
        _RUN_STATE_ADDRESS,
        {
            "strategy": strategy,
            "phase": "initial-materialization-completed",
            "processingRunId": result.processingRunId,
            "initialResponse": result.llm.rawText,
        },
        provenance={
            "kind": "llm-processing-run",
            "processingRunId": result.processingRunId,
        },
    )
    transaction.commit()
    return {
        "strategy": strategy,
        "phase": "initial-materialization-completed",
        "processingRunId": result.processingRunId,
        "response": result.llm.rawText,
    }


def _describe(ctx, _payload):
    """Returns the configured protocol skeleton without starting inference."""
    prompts = _promptDefinitions(ctx)
    analyzers = _requireMapping(ctx.config.get("analyzers"), "analyzers")
    return {
        "strategy": ctx.config.get("strategy"),
        "phases": [
            "initial-materialization",
            "static-analysis-fix-loop",
            "self-audit-and-fix",
            "static-analysis-fix-loop",
            "questionnaire",
            "export",
        ],
        "promptKeys": list(prompts),
        "analyzers": list(analyzers),
        "processExecution": "pending shared Actant ctx.process integration",
    }


def onLoad(ctx):
    """Registers Materialization Test workflow capabilities."""
    ctx.capabilities.register("materializationTest.buildQueryItems@1", _buildQueryItems)
    ctx.capabilities.register("materializationTest.buildQuery@1", _buildQuery)
    ctx.capabilities.register("materializationTest.run@1", _run)
    ctx.capabilities.register("materializationTest.describe@1", _describe)
