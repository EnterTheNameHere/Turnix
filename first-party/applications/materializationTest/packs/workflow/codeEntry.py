# file: first-party/applications/materializationTest/packs/workflow/codeEntry.py ; version: 3
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

from backend.llm.llmTypes import LlmQuery
from backend.process.api import ProcessExecutionError
from backend.processing.runtime import QueryItem

_MEMORY_KEY = "materializationtest"
_RUN_STATE_ADDRESS = "materialization-test/run-state"
_MATERIALIZATION_CALL_LIMIT = 5
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
    """Returns one mapping after validating an application boundary value.

    Args:
        value: Candidate value supplied by configuration or a capability caller.
        name: Human-readable field name used in the boundary error.

    Returns:
        The original mapping without copying it.

    Raises:
        TypeError: If value is not a Mapping.
    """
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object.")
    return value


def _requireString(value: object, name: str) -> str:
    """Returns one exact non-blank string after boundary validation.

    Args:
        value: Candidate string value.
        name: Human-readable field name used in the boundary error.

    Returns:
        The original string, including its whitespace.

    Raises:
        ValueError: If value is not an exact built-in non-blank string.
    """
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

    Args:
        response: Complete raw model response after streaming has finished.

    Returns:
        Exact text between the opening and closing fences.

    Raises:
        TypeError: If response is not an exact built-in string.
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
    """Loads and validates the deliberately simple v1 prompt document.

    Args:
        ctx: Active CodeEntryContext providing mediated configuration and I/O.

    Returns:
        Required prompt strings keyed by their stable v1 names.
    """
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
    """Returns validated provider selection for this benchmark run.

    Args:
        ctx: Active CodeEntryContext exposing application configuration.

    Returns:
        Provider name, optional model name, and provider options mapping.
    """
    llm = _requireMapping(ctx.config.get("llm"), "llm")
    provider = _requireString(llm.get("provider"), "llm.provider")
    model = llm.get("model")
    if model is not None and type(model) is not str:
        raise TypeError("llm.model must be null or a string.")
    options = llm.get("providerOptions", {})
    return provider, model, _requireMapping(options, "llm.providerOptions")


def _analyzerDefinitions(ctx) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Returns analyzer logical tools and argv templates in configured order.

    Args:
        ctx: Active CodeEntryContext exposing application configuration.

    Returns:
        Tuples of analyzer label, logical process tool name, and argument
        template. ``{source}`` is retained for per-attempt substitution.

    Raises:
        TypeError: If analyzer definitions or argument lists have wrong types.
        ValueError: If a required analyzer or source placeholder is missing.
    """
    analyzers = _requireMapping(ctx.config.get("analyzers"), "analyzers")
    result: list[tuple[str, str, tuple[str, ...]]] = []
    for analyzerName in ("ruff", "ty"):
        definition = _requireMapping(analyzers.get(analyzerName), f"analyzers.{analyzerName}")
        toolName = _requireString(definition.get("toolId"), f"analyzers.{analyzerName}.toolId")
        rawArguments = definition.get("arguments")
        if not isinstance(rawArguments, list):
            raise TypeError(f"analyzers.{analyzerName}.arguments must be a list.")
        arguments: list[str] = []
        for index, argument in enumerate(rawArguments):
            if type(argument) is not str:
                raise TypeError(
                    f"analyzers.{analyzerName}.arguments[{index}] must be a string.",
                )
            arguments.append(argument)
        if not any("{source}" in argument for argument in arguments):
            raise ValueError(f"analyzers.{analyzerName}.arguments must reference {{source}}.")
        result.append((analyzerName, toolName, tuple(arguments)))
    return tuple(result)


def _buildQueryItems(_ctx, payload):
    """Builds phase-specific QueryItems from authoritative workflow input.

    Initial materialization receives grounding, the test instruction, and the
    fixed source-output protocol. Analyzer repair receives the applicable
    grounding, the current exact candidate, current analyzer evidence, the
    historical correction instruction/template, and the same output protocol.

    Args:
        _ctx: Unused active CodeEntryContext required by capability signature.
        payload: BUILD_QUERY_ITEMS request supplied by LlmProcessingPipeline.

    Returns:
        Ordered QueryItems forming the Actant-native inference projection.
    """
    request = _requireMapping(payload, "BUILD_QUERY_ITEMS payload")
    inputValue = _requireMapping(request.get("input"), "BUILD_QUERY_ITEMS input")
    phase = _requireString(inputValue.get("phase"), "phase")

    if phase == "initial-materialization":
        parts = (
            ("grounding", "grounding", inputValue.get("grounding")),
            ("initial-materialization", "instruction", inputValue.get("initialMaterialization")),
            ("source-output-protocol", "output-protocol", inputValue.get("sourceProtocol")),
        )
    elif phase == "materialization-repair":
        parts = (
            ("grounding", "grounding", inputValue.get("grounding")),
            ("current-source", "source", inputValue.get("currentSource")),
            ("static-analysis-report", "diagnostics", inputValue.get("staticAnalysisReport")),
            ("source-output-protocol", "output-protocol", inputValue.get("sourceProtocol")),
        )
    else:
        raise ValueError(f"Unsupported materialization phase: {phase!r}.")

    return [
        QueryItem(
            itemId=f"materialization-test:{itemId}",
            kind=kind,
            content=_requireString(content, itemId),
            metadata={"phase": phase, "role": kind},
        )
        for itemId, kind, content in parts
    ]


def _buildQuery(_ctx, payload):
    """Renders accepted QueryItems into the exact v1 text/plain model input.

    Args:
        _ctx: Unused active CodeEntryContext required by capability signature.
        payload: BUILD_QUERY request supplied by LlmProcessingPipeline.

    Returns:
        Provider-neutral text/plain LlmQuery with ordered item contents.
    """
    request = _requireMapping(payload, "BUILD_QUERY payload")
    snapshots = request.get("queryItems")
    if not isinstance(snapshots, list):
        raise TypeError("BUILD_QUERY queryItems must be a list.")
    items = [QueryItem.fromSnapshot(snapshot) for snapshot in snapshots]
    phase = "unknown"
    if items:
        candidatePhase = items[0].metadata.get("phase")
        if type(candidatePhase) is str:
            phase = candidatePhase
    return LlmQuery(
        formatId="text/plain",
        payload="\n\n".join(item.content for item in items),
        metadata={
            "application": "materializationTest",
            "phase": phase,
            "strategy": "actant-native",
        },
    )


def _sourceEvidence(source: str) -> dict[str, object]:
    """Builds durable identity evidence for one exact candidate source.

    Args:
        source: Exact extracted source text sent to analyzers.

    Returns:
        UTF-8 byte length and SHA-256 identity of the candidate.
    """
    payload = source.encode("utf-8")
    return {
        "utf8Bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _analyzeSource(ctx, source: str, *, attemptNumber: int) -> dict[str, object]:
    """Materializes and statically analyzes one exact source candidate.

    The source is written to invocation-private ephemeral workspace state and
    the same absolute path is supplied to Ruff and ty. Nonzero tool exit codes
    are normal analyzer evidence. ProcessExecutionError is intentionally not
    caught here because inability to execute an analyzer is infrastructure
    failure rather than a model/code finding.

    Args:
        ctx: Active CodeEntryContext exposing workspace and process facades.
        source: Exact extracted candidate source.
        attemptNumber: One-based materialization call number used in scratch name.

    Returns:
        Analyzer evidence including generic process snapshots and clean status.

    Raises:
        ProcessExecutionError: If Actant cannot execute a configured analyzer.
    """
    sourcePath = ctx.workspace.materializeText(
        f"materialization/attempt-{attemptNumber}/candidate.py",
        source,
    )
    results: dict[str, object] = {}
    clean = True
    for analyzerName, toolName, argumentTemplate in _analyzerDefinitions(ctx):
        arguments = tuple(argument.replace("{source}", sourcePath) for argument in argumentTemplate)
        result = ctx.process.run(toolName, arguments)
        results[analyzerName] = result
        if result.get("exitCode") != 0:
            clean = False
    return {
        "source": _sourceEvidence(source),
        "analyzers": results,
        "clean": clean,
    }


def _renderStaticAnalysisReport(
    template: str,
    source: str,
    analysis: Mapping[str, object],
) -> str:
    """Renders current source and raw analyzer diagnostics for one repair call.

    V1 deliberately keeps the user's historical static-analysis instruction as
    opaque prompt text rather than inventing a prompt DSL. The current exact
    source and both generic analyzer results are appended as explicit evidence.

    Args:
        template: Historical static-analysis/correction prompt text.
        source: Exact current candidate source.
        analysis: Evidence returned by _analyzeSource().

    Returns:
        Complete repair instruction for the next Actant-native inference.
    """
    analyzerResults = _requireMapping(analysis.get("analyzers"), "analysis.analyzers")
    sections = [template, "Current complete Python source:", f"```python\n{source}```"]
    for analyzerName in ("ruff", "ty"):
        result = _requireMapping(analyzerResults.get(analyzerName), f"analysis.{analyzerName}")
        sections.extend(
            (
                f"{analyzerName} exit code: {result.get('exitCode')}",
                f"{analyzerName} stdout:\n{result.get('stdout', '')}",
                f"{analyzerName} stderr:\n{result.get('stderr', '')}",
            ),
        )
    sections.append(
        "Correct the reported static-analysis problems and return the complete resulting file.",
    )
    return "\n\n".join(sections)


def _runSourceInference(
    ctx,
    *,
    inputValue: Mapping[str, object],
    provider: str,
    model: str | None,
    providerOptions: Mapping[str, object],
    streamObserver: object,
):
    """Runs one source-producing ProcessingRun through the shared LLM pipeline.

    Args:
        ctx: Active CodeEntryContext.
        inputValue: Phase-specific authoritative input projected to QueryItems.
        provider: Configured provider name.
        model: Optional configured model name.
        providerOptions: Detached provider options.
        streamObserver: Optional presentation-only stream observer.

    Returns:
        Completed ProcessingResult containing exact raw model response.
    """
    return ctx.llm.runProcessing(
        memoryKey=_MEMORY_KEY,
        inputValue=dict(inputValue),
        buildQueryItemsCapabilityId="materializationTest.buildQueryItems@1",
        buildQueryCapabilityId="materializationTest.buildQuery@1",
        providerName=provider,
        model=model,
        providerOptions=providerOptions,
        streamObserver=streamObserver,
    )


def _attemptRecord(
    *,
    callNumber: int,
    processingRunId: str,
    rawResponse: str,
    source: str | None,
    extraction: Mapping[str, object],
    analysis: Mapping[str, object] | None,
) -> dict[str, object]:
    """Builds immutable-shaped durable evidence for one source-producing call.

    Args:
        callNumber: One-based call number within the materialization budget.
        processingRunId: Shared pipeline evidence identity.
        rawResponse: Complete raw model response.
        source: Exact extracted source, or None after extraction failure.
        extraction: Extraction outcome evidence.
        analysis: Analyzer evidence, or None when analyzers were not reached.

    Returns:
        JSON-compatible attempt evidence suitable for authoritative state.
    """
    record: dict[str, object] = {
        "callNumber": callNumber,
        "processingRunId": processingRunId,
        "rawResponse": rawResponse,
        "sourceExtraction": dict(extraction),
    }
    if source is not None:
        record["source"] = source
        record["sourceIdentity"] = _sourceEvidence(source)
    if analysis is not None:
        record["staticAnalysis"] = dict(analysis)
    return record


def _run(ctx, payload):
    """Runs Actant-native initial materialization and analyzer repair loop.

    Each valid source response is materialized exactly once into ephemeral
    workspace state and analyzed by both configured analyzers. Dirty analyzer
    results consume another source-producing call until both tools are clean or
    the five-call budget is exhausted. Any malformed source response terminates
    this phase immediately without a format-repair call. Analyzer execution
    failure is classified separately from analyzer findings and is retained in
    run state before the Job reports success; the questionnaire phase will later
    be allowed to continue from these outcomes.

    Args:
        ctx: Active CodeEntryContext.
        payload: Optional run request containing presentation streamObserver.

    Returns:
        Authoritative materialization phase state and complete attempt evidence.

    Raises:
        NotImplementedError: If Classic strategy is selected before materialized.
    """
    request = {} if payload is None else _requireMapping(payload, "Materialization run request")
    strategy = _requireString(ctx.config.get("strategy"), "strategy")
    if strategy not in {"classic", "actant-native"}:
        raise ValueError("strategy must be 'classic' or 'actant-native'.")
    if strategy != "actant-native":
        raise NotImplementedError("Classic strategy is scaffolded but not materialized yet.")

    prompts = _promptDefinitions(ctx)
    provider, model, providerOptions = _llmConfig(ctx)
    attempts: list[dict[str, object]] = []
    currentSource: str | None = None
    currentAnalysis: Mapping[str, object] | None = None
    materializationOutcome = "analyzer-limit-reached"
    phase = "initial-materialization-failed"
    infrastructureError: dict[str, object] | None = None

    for callNumber in range(1, _MATERIALIZATION_CALL_LIMIT + 1):
        if callNumber == 1:
            inputValue: Mapping[str, object] = {
                "phase": "initial-materialization",
                "grounding": prompts["grounding"],
                "initialMaterialization": prompts["initialMaterialization"],
                "sourceProtocol": _SOURCE_FENCE_INSTRUCTION,
            }
        else:
            assert currentSource is not None
            assert currentAnalysis is not None
            inputValue = {
                "phase": "materialization-repair",
                "grounding": prompts["grounding"],
                "currentSource": currentSource,
                "staticAnalysisReport": _renderStaticAnalysisReport(
                    prompts["staticAnalysisReport"],
                    currentSource,
                    currentAnalysis,
                ),
                "sourceProtocol": _SOURCE_FENCE_INSTRUCTION,
            }

        result = _runSourceInference(
            ctx,
            inputValue=inputValue,
            provider=provider,
            model=model,
            providerOptions=providerOptions,
            streamObserver=request.get("streamObserver"),
        )
        try:
            source = _extractPythonSource(result.llm.rawText)
        except SourceExtractionError as err:
            extraction = {"outcome": "failed", "reason": str(err)}
            attempts.append(
                _attemptRecord(
                    callNumber=callNumber,
                    processingRunId=result.processingRunId,
                    rawResponse=result.llm.rawText,
                    source=None,
                    extraction=extraction,
                    analysis=None,
                ),
            )
            materializationOutcome = "extraction-failed"
            phase = "initial-materialization-failed"
            break

        extraction = {"outcome": "accepted", **_sourceEvidence(source)}
        currentSource = source
        try:
            currentAnalysis = _analyzeSource(ctx, source, attemptNumber=callNumber)
        except ProcessExecutionError as err:
            infrastructureError = {
                "type": type(err).__name__,
                "message": str(err),
            }
            attempts.append(
                _attemptRecord(
                    callNumber=callNumber,
                    processingRunId=result.processingRunId,
                    rawResponse=result.llm.rawText,
                    source=source,
                    extraction=extraction,
                    analysis=None,
                ),
            )
            materializationOutcome = "analyzer-execution-failed"
            phase = "initial-materialization-failed"
            break

        attempts.append(
            _attemptRecord(
                callNumber=callNumber,
                processingRunId=result.processingRunId,
                rawResponse=result.llm.rawText,
                source=source,
                extraction=extraction,
                analysis=currentAnalysis,
            ),
        )
        if currentAnalysis.get("clean") is True:
            materializationOutcome = "clean"
            phase = "initial-materialization-clean"
            break
        if callNumber == _MATERIALIZATION_CALL_LIMIT:
            materializationOutcome = "analyzer-limit-reached"
            phase = "initial-materialization-failed"

    state: dict[str, object] = {
        "strategy": strategy,
        "phase": phase,
        "materializationOutcome": materializationOutcome,
        "materializationCallCount": len(attempts),
        "materializationCallLimit": _MATERIALIZATION_CALL_LIMIT,
        "materializationAttempts": attempts,
        "selfAuditOutcome": "not-reached",
        "questionnaireOutcome": "not-reached",
    }
    if currentSource is not None:
        state["currentSource"] = currentSource
        state["currentSourceIdentity"] = _sourceEvidence(currentSource)
    if infrastructureError is not None:
        state["analyzerExecutionError"] = infrastructureError

    transaction = ctx.memory.openTransaction()
    transaction.set(
        _RUN_STATE_ADDRESS,
        state,
        provenance={
            "kind": "materialization-test-run",
            "materializationOutcome": materializationOutcome,
        },
    )
    transaction.commit()
    return state


def _describe(ctx, _payload):
    """Returns the configured protocol skeleton without starting inference.

    Args:
        ctx: Active CodeEntryContext.
        _payload: Unused capability payload.

    Returns:
        JSON-compatible description of currently materialized workflow behavior.
    """
    prompts = _promptDefinitions(ctx)
    analyzers = _analyzerDefinitions(ctx)
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
            "materializationAndRepairs": _MATERIALIZATION_CALL_LIMIT,
            "selfAuditAndRepairs": 5,
        },
        "questionnaireRunsAfterMaterializationFailure": True,
        "promptKeys": list(prompts),
        "analyzers": [name for name, _toolName, _arguments in analyzers],
        "processExecution": "Actant ctx.workspace + ctx.process",
    }


def onLoad(ctx):
    """Registers Materialization Test workflow capabilities for this Pack load.

    Args:
        ctx: Registration-enabled CodeEntryContext valid only for onLoad.
    """
    ctx.capabilities.register("materializationTest.buildQueryItems@1", _buildQueryItems)
    ctx.capabilities.register("materializationTest.buildQuery@1", _buildQuery)
    ctx.capabilities.register("materializationTest.run@1", _run)
    ctx.capabilities.register("materializationTest.describe@1", _describe)
