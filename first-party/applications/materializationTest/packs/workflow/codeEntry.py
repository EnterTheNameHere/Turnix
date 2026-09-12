# file: first-party/applications/materializationTest/packs/workflow/codeEntry.py ; version: 2
from __future__ import annotations

import re
from collections.abc import Mapping

from backend.llm.llmTypes import LlmQuery
from backend.processing.runtime import QueryItem

_MEMORY_KEY = "materializationtest"
_RUN_STATE_ADDRESS = "materialization-test/run-state"
_SOURCE_FENCE_INSTRUCTION = (
    "Return the complete materialized Python file in exactly one fenced Python "
    "code block using ```python. The code block must be non-empty. Do not emit "
    "any other fenced code blocks. Reasoning or other prose, if any, must remain "
    "outside the code block."
)
_FENCE_PATTERN = re.compile(r"(?m)^[ \t]*```([^\r\n`]*)\r?\n")


class SourceExtractionError(ValueError):
    """Reports that a source-producing model response violated the test protocol.

    The exception represents model-output failure, not infrastructure failure.
    Callers must preserve the complete raw response as experiment evidence and
    must not attempt heuristic recovery or consume another repair call merely to
    repair response formatting.
    """


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


def _extractPythonSource(response: str) -> str:
    """Extracts the one protocol-valid Python fence from a completed response.

    Exactly one fenced code block may occur anywhere in the response. Its
    opening info string must be exactly ``python`` after surrounding whitespace
    is removed, its closing fence must be a standalone triple-backtick line,
    and its body must contain non-whitespace source. Prose outside the fence is
    permitted and remains part of the separately retained raw response.

    The function deliberately performs no Markdown recovery, source inference,
    block selection, dedenting, newline normalization, or syntax validation.
    The extracted body is therefore the exact source artifact presented to the
    static analyzers.

    Raises:
        SourceExtractionError: If the response has zero or multiple code blocks,
            uses a non-Python or malformed fence, or contains empty source.
    """
    if type(response) is not str:
        raise TypeError("Model response must be a string.")

    openings = list(_FENCE_PATTERN.finditer(response))
    if len(openings) != 1:
        raise SourceExtractionError(
            f"Expected exactly one fenced code block; found {len(openings)} opening fences.",
        )

    opening = openings[0]
    if opening.group(1).strip() != "python":
        raise SourceExtractionError("The single fenced code block must use the python info string.")

    bodyStart = opening.end()
    closingPattern = re.compile(r"(?m)^[ \t]*```[ \t]*(?:\r?\n|$)")
    closing = closingPattern.search(response, bodyStart)
    if closing is None:
        raise SourceExtractionError("The Python code block is not closed by a standalone triple-backtick fence.")

    if _FENCE_PATTERN.search(response, closing.end()) is not None:
        raise SourceExtractionError("The response contains more than one fenced code block.")

    trailing = response[closing.end():]
    if re.search(r"(?m)^[ \t]*```", trailing) is not None:
        raise SourceExtractionError("The response contains an additional malformed fenced code block.")

    source = response[bodyStart:closing.start()]
    if not source.strip():
        raise SourceExtractionError("The Python code block is empty or whitespace-only.")
    return source


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
    sourceProtocol = _requireString(inputValue.get("sourceProtocol"), "sourceProtocol")
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
        QueryItem(
            itemId="materialization-test:source-output-protocol",
            kind="output-protocol",
            content=sourceProtocol,
            metadata={"phase": phase, "role": "output-protocol"},
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
    """Runs initial materialization and records response/extraction evidence.

    Streaming observation is presentation-only. Source extraction starts only
    after runProcessing() has returned the complete raw response. A malformed
    source response is retained as authoritative experiment evidence and ends
    this materialization phase without analyzer execution or format-repair
    inference.
    """
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
            "sourceProtocol": _SOURCE_FENCE_INSTRUCTION,
        },
        buildQueryItemsCapabilityId="materializationTest.buildQueryItems@1",
        buildQueryCapabilityId="materializationTest.buildQuery@1",
        providerName=provider,
        model=model,
        providerOptions=providerOptions,
        streamObserver=request.get("streamObserver"),
    )

    try:
        source = _extractPythonSource(result.llm.rawText)
    except SourceExtractionError as err:
        extraction = {
            "outcome": "failed",
            "reason": str(err),
        }
        phase = "initial-materialization-failed"
        materializationOutcome = "extraction-failed"
        source = None
    else:
        extraction = {
            "outcome": "accepted",
            "utf8Bytes": len(source.encode("utf-8")),
        }
        phase = "initial-materialization-source-accepted"
        materializationOutcome = "awaiting-static-analysis"

    state: dict[str, object] = {
        "strategy": strategy,
        "phase": phase,
        "materializationOutcome": materializationOutcome,
        "materializationCallCount": 1,
        "materializationCallLimit": 5,
        "processingRunId": result.processingRunId,
        "initialResponse": result.llm.rawText,
        "sourceExtraction": extraction,
    }
    if source is not None:
        state["currentSource"] = source

    transaction = ctx.memory.openTransaction()
    transaction.set(
        _RUN_STATE_ADDRESS,
        state,
        provenance={
            "kind": "llm-processing-run",
            "processingRunId": result.processingRunId,
        },
    )
    transaction.commit()
    return state


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
        "sourceResponseProtocol": {
            "requiredCodeBlocks": 1,
            "language": "python",
            "nonWhitespaceSourceRequired": True,
            "malformedResponseTerminatesPhase": True,
        },
        "callLimits": {
            "materializationAndRepairs": 5,
            "selfAuditAndRepairs": 5,
        },
        "questionnaireRunsAfterMaterializationFailure": True,
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
