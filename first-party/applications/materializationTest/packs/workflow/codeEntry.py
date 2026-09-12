# file: first-party/applications/materializationTest/packs/workflow/codeEntry.py ; version: 8
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from backend.llm.errors import LlmError
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
    """Report a completed source response that violates benchmark extraction rules."""


def _requireMapping(value: object, name: str) -> Mapping[str, object]:
    """Validate an object-valued application boundary and return it unchanged."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object.")
    return value


def _requireString(value: object, name: str) -> str:
    """Validate an exact non-blank string boundary and preserve its contents."""
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-blank string.")
    return value


def _requireQuestions(value: object, name: str) -> tuple[str, ...]:
    """Validate and freeze explicit ordered questionnaire boundaries."""
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list.")
    if not value:
        raise ValueError(f"{name} must contain at least one question.")
    questions: list[str] = []
    for index, question in enumerate(value):
        if type(question) is not str:
            raise TypeError(f"{name}[{index}] must be a string.")
        if not question.strip():
            raise ValueError(f"{name}[{index}] must be a non-blank string.")
        questions.append(question)
    return tuple(questions)


def _extractPythonSource(response: str) -> str:
    """Extract the one protocol-valid Python fence from a completed response."""
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


def _promptDefinitions(ctx) -> dict[str, object]:
    """Load and validate the benchmark prompt/test definition document."""
    promptPath = _requireString(ctx.config.get("promptsFile"), "promptsFile")
    definitions = _requireMapping(ctx.io.readJson(promptPath), "Prompt definitions")
    result: dict[str, object] = {}
    for key in (
        "grounding",
        "initialMaterialization",
        "staticAnalysisReport",
        "searchForBugsAndFix",
        "questionnaireInstructions",
    ):
        result[key] = _requireString(definitions.get(key), f"Prompt definition {key!r}")
    result["questionnaire"] = _requireQuestions(
        definitions.get("questionnaire"),
        "Prompt definition 'questionnaire'",
    )
    return result


def _promptText(prompts: Mapping[str, object], key: str) -> str:
    """Return one already-loaded textual prompt member after defensive validation."""
    return _requireString(prompts.get(key), f"Prompt definition {key!r}")


def _questionnaire(prompts: Mapping[str, object]) -> tuple[str, ...]:
    """Return the validated ordered questionnaire without deriving boundaries."""
    value = prompts.get("questionnaire")
    if not isinstance(value, tuple) or not all(type(question) is str for question in value):
        raise TypeError("Loaded questionnaire must be a tuple of strings.")
    return value


def _llmConfig(ctx) -> tuple[str, str | None, Mapping[str, object]]:
    """Return validated provider, optional model, and provider options for the run."""
    llm = _requireMapping(ctx.config.get("llm"), "llm")
    provider = _requireString(llm.get("provider"), "llm.provider")
    model = llm.get("model")
    if model is not None and type(model) is not str:
        raise TypeError("llm.model must be null or a string.")
    return provider, model, _requireMapping(llm.get("providerOptions", {}), "llm.providerOptions")


def _analyzerDefinitions(ctx) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Return Ruff and ty logical tools plus exact configured argument templates."""
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
    """Build the exact phase-specific QueryItems projected into one native inference."""
    request = _requireMapping(payload, "BUILD_QUERY_ITEMS payload")
    inputValue = _requireMapping(request.get("input"), "BUILD_QUERY_ITEMS input")
    phase = _requireString(inputValue.get("phase"), "phase")
    if phase == "initial-materialization":
        parts = (
            ("grounding", "grounding", inputValue.get("grounding")),
            ("requirements", "requirements", inputValue.get("requirements")),
            ("source-output-protocol", "output-protocol", inputValue.get("sourceProtocol")),
        )
    elif phase in {"materialization-repair", "self-audit-repair"}:
        parts = (
            ("grounding", "grounding", inputValue.get("grounding")),
            ("requirements", "requirements", inputValue.get("requirements")),
            ("current-source", "source", inputValue.get("currentSource")),
            ("static-analysis-report", "diagnostics", inputValue.get("staticAnalysisReport")),
            ("source-output-protocol", "output-protocol", inputValue.get("sourceProtocol")),
        )
    elif phase == "self-audit":
        parts = (
            ("grounding", "grounding", inputValue.get("grounding")),
            ("requirements", "requirements", inputValue.get("requirements")),
            ("current-source", "source", inputValue.get("currentSource")),
            ("self-audit", "instruction", inputValue.get("searchForBugsAndFix")),
            ("source-output-protocol", "output-protocol", inputValue.get("sourceProtocol")),
        )
    elif phase == "questionnaire":
        partsList: list[tuple[str, str, object]] = [
            ("grounding", "grounding", inputValue.get("grounding")),
            ("requirements", "requirements", inputValue.get("requirements")),
        ]
        currentSource = inputValue.get("currentSource")
        if currentSource is not None:
            partsList.append(("current-source", "source", currentSource))
        else:
            partsList.append(("materialization-state", "state", inputValue.get("materializationState")))
        partsList.extend((
            ("questionnaire-instructions", "instruction", inputValue.get("questionnaireInstructions")),
            ("question", "question", inputValue.get("question")),
        ))
        parts = tuple(partsList)
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
    """Render accepted QueryItems into the exact provider-neutral text query."""
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
    """Return stable UTF-8 size and SHA-256 identity for an exact source string."""
    payload = source.encode("utf-8")
    return {"utf8Bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _analyzeSource(ctx, source: str, *, attemptNumber: int, phaseName: str = "materialization") -> dict[str, object]:
    """Materialize one exact candidate into scratch and run both configured analyzers."""
    sourcePath = ctx.workspace.materializeText(f"{phaseName}/attempt-{attemptNumber}/candidate.py", source)
    results: dict[str, object] = {}
    clean = True
    for analyzerName, toolName, argumentTemplate in _analyzerDefinitions(ctx):
        arguments = tuple(argument.replace("{source}", sourcePath) for argument in argumentTemplate)
        result = ctx.process.run(toolName, arguments)
        results[analyzerName] = result
        if result.get("exitCode") != 0:
            clean = False
    return {"source": _sourceEvidence(source), "analyzers": results, "clean": clean}


def _renderStaticAnalysisReport(template: str, analysis: Mapping[str, object]) -> str:
    """Render current analyzer diagnostics without duplicating current source."""
    analyzerResults = _requireMapping(analysis.get("analyzers"), "analysis.analyzers")
    sections = [template]
    for analyzerName in ("ruff", "ty"):
        result = _requireMapping(analyzerResults.get(analyzerName), f"analysis.{analyzerName}")
        sections.extend((
            f"{analyzerName} exit code: {result.get('exitCode')}",
            f"{analyzerName} stdout:\n{result.get('stdout', '')}",
            f"{analyzerName} stderr:\n{result.get('stderr', '')}",
        ))
    sections.append("Correct the reported static-analysis problems and return the complete resulting file.")
    return "\n\n".join(sections)


def _runInference(ctx, *, inputValue: Mapping[str, object], provider: str, model: str | None, providerOptions: Mapping[str, object], streamObserver: object):
    """Execute one benchmark inference as a shared transactional ProcessingRun."""
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


def _attemptRecord(*, callNumber: int, processingRunId: str, rawResponse: str, source: str | None, extraction: Mapping[str, object], analysis: Mapping[str, object] | None) -> dict[str, object]:
    """Build durable evidence for one completed source-producing inference attempt."""
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


def _runSelfAudit(ctx, *, prompts: Mapping[str, object], initialSource: str, provider: str, model: str | None, providerOptions: Mapping[str, object], streamObserver: object) -> dict[str, object]:
    """Run self-audit and its independent five-call analyzer-repair budget."""
    attempts: list[dict[str, object]] = []
    currentSource = initialSource
    currentAnalysis: Mapping[str, object] | None = None
    outcome = "analyzer-limit-reached"
    infrastructureError: dict[str, object] | None = None
    requirements = _promptText(prompts, "initialMaterialization")
    for callNumber in range(1, _SELF_AUDIT_CALL_LIMIT + 1):
        if callNumber == 1:
            inputValue: Mapping[str, object] = {
                "phase": "self-audit",
                "grounding": _promptText(prompts, "grounding"),
                "requirements": requirements,
                "currentSource": currentSource,
                "searchForBugsAndFix": _promptText(prompts, "searchForBugsAndFix"),
                "sourceProtocol": _SOURCE_FENCE_INSTRUCTION,
            }
        else:
            assert currentAnalysis is not None
            inputValue = {
                "phase": "self-audit-repair",
                "grounding": _promptText(prompts, "grounding"),
                "requirements": requirements,
                "currentSource": currentSource,
                "staticAnalysisReport": _renderStaticAnalysisReport(_promptText(prompts, "staticAnalysisReport"), currentAnalysis),
                "sourceProtocol": _SOURCE_FENCE_INSTRUCTION,
            }
        result = _runInference(ctx, inputValue=inputValue, provider=provider, model=model, providerOptions=providerOptions, streamObserver=streamObserver)
        try:
            source = _extractPythonSource(result.llm.rawText)
        except SourceExtractionError as err:
            attempts.append(_attemptRecord(callNumber=callNumber, processingRunId=result.processingRunId, rawResponse=result.llm.rawText, source=None, extraction={"outcome": "failed", "reason": str(err)}, analysis=None))
            outcome = "extraction-failed"
            break
        extraction = {"outcome": "accepted", **_sourceEvidence(source)}
        currentSource = source
        try:
            currentAnalysis = _analyzeSource(ctx, source, attemptNumber=callNumber, phaseName="self-audit")
        except ProcessExecutionError as err:
            infrastructureError = {"type": type(err).__name__, "message": str(err)}
            attempts.append(_attemptRecord(callNumber=callNumber, processingRunId=result.processingRunId, rawResponse=result.llm.rawText, source=source, extraction=extraction, analysis=None))
            outcome = "analyzer-execution-failed"
            break
        attempts.append(_attemptRecord(callNumber=callNumber, processingRunId=result.processingRunId, rawResponse=result.llm.rawText, source=source, extraction=extraction, analysis=currentAnalysis))
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


def _materializationStateForQuestionnaire(state: Mapping[str, object]) -> str:
    """Describe failed implementation truthfully when no valid source artifact exists."""
    return (
        "No valid Python source artifact was extracted during materialization.\n"
        f"Materialization outcome: {state.get('materializationOutcome')}.\n"
        f"Self-audit outcome: {state.get('selfAuditOutcome')}."
    )


def _runQuestionnaire(ctx, *, prompts: Mapping[str, object], state: Mapping[str, object], provider: str, model: str | None, providerOptions: Mapping[str, object], streamObserver: object) -> dict[str, object]:
    """Run one native inference per requirement against one frozen artifact."""
    questions = _questionnaire(prompts)
    sourceValue = state.get("currentSource")
    currentSource = sourceValue if type(sourceValue) is str else None
    materializationState = None if currentSource is not None else _materializationStateForQuestionnaire(state)
    answers: list[dict[str, object]] = []
    for index, question in enumerate(questions):
        inputValue: dict[str, object] = {
            "phase": "questionnaire",
            "grounding": _promptText(prompts, "grounding"),
            "requirements": _promptText(prompts, "initialMaterialization"),
            "questionnaireInstructions": _promptText(prompts, "questionnaireInstructions"),
            "question": question,
        }
        if currentSource is not None:
            inputValue["currentSource"] = currentSource
        else:
            assert materializationState is not None
            inputValue["materializationState"] = materializationState
        try:
            result = _runInference(ctx, inputValue=inputValue, provider=provider, model=model, providerOptions=providerOptions, streamObserver=streamObserver)
        except LlmError as err:
            return {
                "outcome": "inference-failed" if not answers else "partially-completed",
                "questionCount": len(questions),
                "completedCount": len(answers),
                "answers": answers,
                "failure": {"questionIndex": index, "question": question, "type": type(err).__name__, "message": str(err)},
            }
        answers.append({"questionIndex": index, "question": question, "processingRunId": result.processingRunId, "rawResponse": result.llm.rawText})
    return {"outcome": "completed", "questionCount": len(questions), "completedCount": len(answers), "answers": answers}


def _run(ctx, payload):
    """Run the complete Actant-native benchmark through questionnaire evidence."""
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
    requirements = _promptText(prompts, "initialMaterialization")
    for callNumber in range(1, _MATERIALIZATION_CALL_LIMIT + 1):
        if callNumber == 1:
            inputValue: Mapping[str, object] = {
                "phase": "initial-materialization",
                "grounding": _promptText(prompts, "grounding"),
                "requirements": requirements,
                "sourceProtocol": _SOURCE_FENCE_INSTRUCTION,
            }
        else:
            assert currentSource is not None and currentAnalysis is not None
            inputValue = {
                "phase": "materialization-repair",
                "grounding": _promptText(prompts, "grounding"),
                "requirements": requirements,
                "currentSource": currentSource,
                "staticAnalysisReport": _renderStaticAnalysisReport(_promptText(prompts, "staticAnalysisReport"), currentAnalysis),
                "sourceProtocol": _SOURCE_FENCE_INSTRUCTION,
            }
        result = _runInference(ctx, inputValue=inputValue, provider=provider, model=model, providerOptions=providerOptions, streamObserver=streamObserver)
        try:
            source = _extractPythonSource(result.llm.rawText)
        except SourceExtractionError as err:
            attempts.append(_attemptRecord(callNumber=callNumber, processingRunId=result.processingRunId, rawResponse=result.llm.rawText, source=None, extraction={"outcome": "failed", "reason": str(err)}, analysis=None))
            materializationOutcome = "extraction-failed"
            break
        extraction = {"outcome": "accepted", **_sourceEvidence(source)}
        currentSource = source
        try:
            currentAnalysis = _analyzeSource(ctx, source, attemptNumber=callNumber)
        except ProcessExecutionError as err:
            infrastructureError = {"type": type(err).__name__, "message": str(err)}
            attempts.append(_attemptRecord(callNumber=callNumber, processingRunId=result.processingRunId, rawResponse=result.llm.rawText, source=source, extraction=extraction, analysis=None))
            materializationOutcome = "analyzer-execution-failed"
            break
        attempts.append(_attemptRecord(callNumber=callNumber, processingRunId=result.processingRunId, rawResponse=result.llm.rawText, source=source, extraction=extraction, analysis=currentAnalysis))
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
        "questionnaireQuestionCount": len(_questionnaire(prompts)),
        "questionnaireCompletedCount": 0,
        "questionnaireAnswers": [],
    }
    if currentSource is not None:
        state["currentSource"] = currentSource
        state["currentSourceIdentity"] = _sourceEvidence(currentSource)
    if infrastructureError is not None:
        state["materializationAnalyzerExecutionError"] = infrastructureError
    if materializationOutcome == "clean":
        assert currentSource is not None
        selfAudit = _runSelfAudit(ctx, prompts=prompts, initialSource=currentSource, provider=provider, model=model, providerOptions=providerOptions, streamObserver=streamObserver)
        state["selfAuditOutcome"] = selfAudit["outcome"]
        state["selfAuditCallCount"] = selfAudit["callCount"]
        state["selfAuditAttempts"] = selfAudit["attempts"]
        state["currentSource"] = selfAudit["currentSource"]
        state["currentSourceIdentity"] = selfAudit["currentSourceIdentity"]
        if "analyzerExecutionError" in selfAudit:
            state["selfAuditAnalyzerExecutionError"] = selfAudit["analyzerExecutionError"]
    questionnaire = _runQuestionnaire(ctx, prompts=prompts, state=state, provider=provider, model=model, providerOptions=providerOptions, streamObserver=streamObserver)
    state["questionnaireOutcome"] = questionnaire["outcome"]
    state["questionnaireQuestionCount"] = questionnaire["questionCount"]
    state["questionnaireCompletedCount"] = questionnaire["completedCount"]
    state["questionnaireAnswers"] = questionnaire["answers"]
    if "failure" in questionnaire:
        state["questionnaireFailure"] = questionnaire["failure"]
    state["phase"] = "questionnaire-completed" if questionnaire["outcome"] == "completed" else "questionnaire-failed"
    transaction = ctx.memory.openTransaction()
    transaction.set(
        _RUN_STATE_ADDRESS,
        state,
        provenance={
            "kind": "materialization-test-run",
            "materializationOutcome": materializationOutcome,
            "selfAuditOutcome": state["selfAuditOutcome"],
            "questionnaireOutcome": state["questionnaireOutcome"],
        },
    )
    transaction.commit()
    return state


def _describe(ctx, _payload):
    """Return the configured protocol skeleton without performing model inference."""
    prompts = _promptDefinitions(ctx)
    analyzers = _analyzerDefinitions(ctx)
    return {
        "strategy": ctx.config.get("strategy"),
        "phases": ["initial-materialization", "static-analysis-fix-loop", "self-audit-and-fix", "static-analysis-fix-loop", "questionnaire", "export"],
        "sourceResponseProtocol": {"requiredCodeBlocks": 1, "language": "python", "nonWhitespaceSourceRequired": True, "malformedResponseTerminatesPhase": True},
        "callLimits": {"materializationAndRepairs": _MATERIALIZATION_CALL_LIMIT, "selfAuditAndRepairs": _SELF_AUDIT_CALL_LIMIT},
        "questionnaireRunsAfterMaterializationFailure": True,
        "questionnaireMode": "one-inference-per-question",
        "questionnaireQuestionCount": len(_questionnaire(prompts)),
        "promptKeys": list(prompts),
        "analyzers": [name for name, _toolName, _arguments in analyzers],
        "processExecution": "Actant ctx.workspace + ctx.process",
    }


def onLoad(ctx):
    """Register workflow capabilities for the lifetime of this loaded CodeEntry."""
    ctx.capabilities.register("materializationTest.buildQueryItems@1", _buildQueryItems)
    ctx.capabilities.register("materializationTest.buildQuery@1", _buildQuery)
    ctx.capabilities.register("materializationTest.run@1", _run)
    ctx.capabilities.register("materializationTest.describe@1", _describe)
