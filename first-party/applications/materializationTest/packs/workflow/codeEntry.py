# file: first-party/applications/materializationTest/packs/workflow/codeEntry.py ; version: 5
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from backend.llm.llmTypes import LlmQuery
from backend.process.api import ProcessExecutionError
from backend.processing.runtime import QueryItem

_MEMORY_KEY = "materializationtest"
_RUN_STATE_ADDRESS = "materialization-test/run-state"
_MATERIALIZATION_CALL_LIMIT = 5
_SELF_AUDIT_CALL_LIMIT = 5
_SOURCE_FENCE_INSTRUCTION = (
    "Return the complete materialized Python file in exactly one fenced Python "
    "code block using ```python. The code block must be non-empty. Do not emit "
    "any other fenced code blocks. Reasoning or other prose, if any, must remain "
    "outside the code block."
)
_FENCE_PATTERN = re.compile(r"(?m)^[ \t]*```([^\r\n`]*)[ \t]*(?:\r?\n|$)")


class SourceExtractionError(ValueError):
    """Reports that a source-producing model response violated the test protocol."""


def _requireMapping(value: object, name: str) -> Mapping[str, object]:
    """Returns one mapping after validating an application boundary value."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object.")
    return value


def _requireString(value: object, name: str) -> str:
    """Returns one exact non-blank string after boundary validation."""
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-blank string.")
    return value


def _extractPythonSource(response: str) -> str:
    """Extracts the one protocol-valid Python fence from a completed response.

    Fence lines are parsed as structural tokens rather than treating every
    triple-backtick line as an opening fence. A valid response therefore has
    exactly two fence tokens: one ``python`` opening and one empty-info closing.
    The exact text between those tokens is returned without newline conversion.
    """
    if type(response) is not str:
        raise TypeError("Model response must be a string.")
    fences = list(_FENCE_PATTERN.finditer(response))
    if not fences:
        raise SourceExtractionError("Expected exactly one fenced code block; found 0 opening fences.")
    opening = fences[0]
    if opening.group(1).strip() != "python":
        raise SourceExtractionError("The single fenced code block must use the python info string.")
    if len(fences) == 1:
        raise SourceExtractionError("The Python code block is not closed by a standalone triple-backtick fence.")
    closing = fences[1]
    if closing.group(1).strip():
        raise SourceExtractionError("The Python code block is not closed by a standalone triple-backtick fence.")
    if len(fences) > 2:
        third = fences[2]
        if third.end() == len(response) and not response[third.start():].endswith(("\n", "\r")):
            raise SourceExtractionError("The response contains an additional malformed fenced code block.")
        raise SourceExtractionError("The response contains more than one fenced code block.")
    source = response[opening.end():closing.start()]
    if not source.strip():
        raise SourceExtractionError("The Python code block is empty or whitespace-only.")
    return source


def _promptDefinitions(ctx) -> dict[str, str]:
    """Loads and validates the deliberately simple v1 prompt document."""
    promptPath = _requireString(ctx.config.get("promptsFile"), "promptsFile")
    definitions = _requireMapping(ctx.io.readJson(promptPath), "Prompt definitions")
    required = (
        "grounding",
        "initialMaterialization",
        "staticAnalysisReport",
        "searchForBugsAndFix",
        "questionnaire",
    )
    return {key: _requireString(definitions.get(key), f"Prompt definition {key!r}") for key in required}


def _llmConfig(ctx) -> tuple[str, str | None, Mapping[str, object]]:
    """Returns validated provider selection for this benchmark run."""
    llm = _requireMapping(ctx.config.get("llm"), "llm")
    provider = _requireString(llm.get("provider"), "llm.provider")
    model = llm.get("model")
    if model is not None and type(model) is not str:
        raise TypeError("llm.model must be null or a string.")
    return provider, model, _requireMapping(llm.get("providerOptions", {}), "llm.providerOptions")


def _analyzerDefinitions(ctx) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Returns analyzer logical tools and argv templates in configured order."""
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
                raise TypeError(f"analyzers.{analyzerName}.arguments[{index}] must be a string.")
            arguments.append(argument)
        if not any("{source}" in argument for argument in arguments):
            raise ValueError(f"analyzers.{analyzerName}.arguments must reference {{source}}.")
        result.append((analyzerName, toolName, tuple(arguments)))
    return tuple(result)


def _buildQueryItems(_ctx, payload):
    """Builds phase-specific QueryItems from authoritative workflow input."""
    request = _requireMapping(payload, "BUILD_QUERY_ITEMS payload")
    inputValue = _requireMapping(request.get("input"), "BUILD_QUERY_ITEMS input")
    phase = _requireString(inputValue.get("phase"), "phase")
    if phase == "initial-materialization":
        parts = (
            ("grounding", "grounding", inputValue.get("grounding")),
            ("initial-materialization", "instruction", inputValue.get("initialMaterialization")),
            ("source-output-protocol", "output-protocol", inputValue.get("sourceProtocol")),
        )
    elif phase in {"materialization-repair", "self-audit-repair"}:
        parts = (
            ("grounding", "grounding", inputValue.get("grounding")),
            ("current-source", "source", inputValue.get("currentSource")),
            ("static-analysis-report", "diagnostics", inputValue.get("staticAnalysisReport")),
            ("source-output-protocol", "output-protocol", inputValue.get("sourceProtocol")),
        )
    elif phase == "self-audit":
        parts = (
            ("grounding", "grounding", inputValue.get("grounding")),
            ("current-source", "source", inputValue.get("currentSource")),
            ("self-audit", "instruction", inputValue.get("searchForBugsAndFix")),
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
    """Renders accepted QueryItems into the exact v1 text/plain model input."""
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
        metadata={"application": "materializationTest", "phase": phase, "strategy": "actant-native"},
    )


def _sourceEvidence(source: str) -> dict[str, object]:
    """Builds durable identity evidence for one exact candidate source."""
    payload = source.encode("utf-8")
    return {"utf8Bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _analyzeSource(
    ctx,
    source: str,
    *,
    attemptNumber: int,
    phaseName: str = "materialization",
) -> dict[str, object]:
    """Materializes and statically analyzes one exact source candidate."""
    sourcePath = ctx.workspace.materializeText(
        f"{phaseName}/attempt-{attemptNumber}/candidate.py",
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
    return {"source": _sourceEvidence(source), "analyzers": results, "clean": clean}


def _renderStaticAnalysisReport(
    template: str,
    source: str,
    analysis: Mapping[str, object],
) -> str:
    """Renders current source and raw analyzer diagnostics for one repair call."""
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
    sections.append("Correct the reported static-analysis problems and return the complete resulting file.")
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
    """Runs one source-producing ProcessingRun through the shared LLM pipeline."""
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
    """Builds durable evidence for one source-producing call."""
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


def _runSelfAudit(
    ctx,
    *,
    prompts: Mapping[str, str],
    initialSource: str,
    provider: str,
    model: str | None,
    providerOptions: Mapping[str, object],
    streamObserver: object,
) -> dict[str, object]:
    """Runs self-audit plus its independent analyzer-repair budget.

    The first call always receives the analyzer-clean materialization and the
    historical bug-search/fix instruction. Subsequent calls receive only the
    latest candidate and latest analyzer report. The phase has its own five-call
    ceiling and the same strict source-response protocol as initial materialization.
    """
    attempts: list[dict[str, object]] = []
    currentSource = initialSource
    currentAnalysis: Mapping[str, object] | None = None
    outcome = "analyzer-limit-reached"
    infrastructureError: dict[str, object] | None = None

    for callNumber in range(1, _SELF_AUDIT_CALL_LIMIT + 1):
        if callNumber == 1:
            inputValue: Mapping[str, object] = {
                "phase": "self-audit",
                "grounding": prompts["grounding"],
                "currentSource": currentSource,
                "searchForBugsAndFix": prompts["searchForBugsAndFix"],
                "sourceProtocol": _SOURCE_FENCE_INSTRUCTION,
            }
        else:
            assert currentAnalysis is not None
            inputValue = {
                "phase": "self-audit-repair",
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
            streamObserver=streamObserver,
        )
        try:
            source = _extractPythonSource(result.llm.rawText)
        except SourceExtractionError as err:
            attempts.append(
                _attemptRecord(
                    callNumber=callNumber,
                    processingRunId=result.processingRunId,
                    rawResponse=result.llm.rawText,
                    source=None,
                    extraction={"outcome": "failed", "reason": str(err)},
                    analysis=None,
                ),
            )
            outcome = "extraction-failed"
            break

        extraction = {"outcome": "accepted", **_sourceEvidence(source)}
        currentSource = source
        try:
            currentAnalysis = _analyzeSource(
                ctx,
                source,
                attemptNumber=callNumber,
                phaseName="self-audit",
            )
        except ProcessExecutionError as err:
            infrastructureError = {"type": type(err).__name__, "message": str(err)}
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
            outcome = "analyzer-execution-failed"
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
            outcome = "clean"
            break

    resultState: dict[str, object] = {
        "outcome": outcome,
        "callCount": len(attempts),
        "callLimit": _SELF_AUDIT_CALL_LIMIT,
        "attempts": attempts,
        "currentSource": currentSource,
        "currentSourceIdentity": _sourceEvidence(currentSource),
    }
    if infrastructureError is not None:
        resultState["analyzerExecutionError"] = infrastructureError
    return resultState


def _run(ctx, payload):
    """Runs Actant-native materialization, repair, and self-audit phases."""
    request = {} if payload is None else _requireMapping(payload, "Materialization run request")
    strategy = _requireString(ctx.config.get("strategy"), "strategy")
    if strategy not in {"classic", "actant-native"}:
        raise ValueError("strategy must be 'classic' or 'actant-native'.")
    if strategy != "actant-native":
        raise NotImplementedError("Classic strategy is scaffolded but not materialized yet.")

    prompts = _promptDefinitions(ctx)
    provider, model, providerOptions = _llmConfig(ctx)
    streamObserver = request.get("streamObserver")
    attempts: list[dict[str, object]] = []
    currentSource: str | None = None
    currentAnalysis: Mapping[str, object] | None = None
    materializationOutcome = "analyzer-limit-reached"
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
            streamObserver=streamObserver,
        )
        try:
            source = _extractPythonSource(result.llm.rawText)
        except SourceExtractionError as err:
            attempts.append(
                _attemptRecord(
                    callNumber=callNumber,
                    processingRunId=result.processingRunId,
                    rawResponse=result.llm.rawText,
                    source=None,
                    extraction={"outcome": "failed", "reason": str(err)},
                    analysis=None,
                ),
            )
            materializationOutcome = "extraction-failed"
            break
        extraction = {"outcome": "accepted", **_sourceEvidence(source)}
        currentSource = source
        try:
            currentAnalysis = _analyzeSource(ctx, source, attemptNumber=callNumber)
        except ProcessExecutionError as err:
            infrastructureError = {"type": type(err).__name__, "message": str(err)}
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
            break

    state: dict[str, object] = {
        "strategy": strategy,
        "materializationOutcome": materializationOutcome,
        "materializationCallCount": len(attempts),
        "materializationCallLimit": _MATERIALIZATION_CALL_LIMIT,
        "materializationAttempts": attempts,
        "selfAuditOutcome": "not-reached",
        "selfAuditCallCount": 0,
        "selfAuditCallLimit": _SELF_AUDIT_CALL_LIMIT,
        "selfAuditAttempts": [],
        "questionnaireOutcome": "not-reached",
    }
    if currentSource is not None:
        state["currentSource"] = currentSource
        state["currentSourceIdentity"] = _sourceEvidence(currentSource)
    if infrastructureError is not None:
        state["materializationAnalyzerExecutionError"] = infrastructureError

    if materializationOutcome == "clean":
        assert currentSource is not None
        selfAudit = _runSelfAudit(
            ctx,
            prompts=prompts,
            initialSource=currentSource,
            provider=provider,
            model=model,
            providerOptions=providerOptions,
            streamObserver=streamObserver,
        )
        state["selfAuditOutcome"] = selfAudit["outcome"]
        state["selfAuditCallCount"] = selfAudit["callCount"]
        state["selfAuditAttempts"] = selfAudit["attempts"]
        state["currentSource"] = selfAudit["currentSource"]
        state["currentSourceIdentity"] = selfAudit["currentSourceIdentity"]
        if "analyzerExecutionError" in selfAudit:
            state["selfAuditAnalyzerExecutionError"] = selfAudit["analyzerExecutionError"]

    if materializationOutcome != "clean":
        state["phase"] = "initial-materialization-failed"
    elif state["selfAuditOutcome"] == "clean":
        state["phase"] = "self-audit-clean"
    else:
        state["phase"] = "self-audit-failed"

    transaction = ctx.memory.openTransaction()
    transaction.set(
        _RUN_STATE_ADDRESS,
        state,
        provenance={
            "kind": "materialization-test-run",
            "materializationOutcome": materializationOutcome,
            "selfAuditOutcome": state["selfAuditOutcome"],
        },
    )
    transaction.commit()
    return state


def _describe(ctx, _payload):
    """Returns the configured protocol skeleton without starting inference."""
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
            "selfAuditAndRepairs": _SELF_AUDIT_CALL_LIMIT,
        },
        "questionnaireRunsAfterMaterializationFailure": True,
        "promptKeys": list(prompts),
        "analyzers": [name for name, _toolName, _arguments in analyzers],
        "processExecution": "Actant ctx.workspace + ctx.process",
    }


def onLoad(ctx):
    """Registers Materialization Test workflow capabilities for this Pack load."""
    ctx.capabilities.register("materializationTest.buildQueryItems@1", _buildQueryItems)
    ctx.capabilities.register("materializationTest.buildQuery@1", _buildQuery)
    ctx.capabilities.register("materializationTest.run@1", _run)
    ctx.capabilities.register("materializationTest.describe@1", _describe)
