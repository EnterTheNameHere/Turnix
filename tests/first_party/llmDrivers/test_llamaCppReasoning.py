# file: tests/first_party/llmDrivers/test_llamaCppReasoning.py ; version: 1
"""LC-02 request/template fidelity and private reasoning retrieval through real processing."""
from __future__ import annotations

import importlib.util
import json
import traceback
from dataclasses import asdict
from pathlib import Path

import pytest

from backend.llm.errors import LlmProviderConnectionError, LlmProviderProtocolError
from backend.llm.llmTypes import LlmCallRequest, LlmQuery
from backend.llm.streamObservation import StreamObservation, StreamOutcome
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.llm.structuredMessages import LlmMessage, LlmMessages
from backend.orchestration import CancellationSignal, ExecutionCancelled
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.values import CommittedValueLayer, MISSING

_PATH = Path(__file__).parents[3] / "first-party/llmDrivers/llamaCpp/structuredCodeEntry.py"
_SPEC = importlib.util.spec_from_file_location("llamaCppReasoningTestAdapter", _PATH)
adapter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adapter)
impl = adapter._impl


def query(structured=True):
    """Build exact messages with Unicode and boundaries that must survive both paths."""
    if structured:
        return LlmMessages((LlmMessage(role="user", content="one\r\né🙂  "),
                            LlmMessage(role="assistant", content="two"),
                            LlmMessage(role="user", content="three"))).toQuery()
    return LlmQuery(formatId="text/plain", payload="one\r\né🙂  ")


def chunk(delta=None, *, finish=None, **metadata):
    """Construct a native single-choice SSE JSON payload without copying production logic."""
    return {"choices": [{"index": 0, "delta": {} if delta is None else delta,
                         "finish_reason": finish}], **metadata}


def lines(chunks, done=True):
    """Serialize realistic llama.cpp one-data-line SSE events with heartbeats."""
    result = [b": heartbeat\n", b"\n"]
    for value in chunks:
        result.extend((b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n", b"\n"))
    if done:
        result.append(b"data: [DONE]\n")
    return result


class Response:
    """Observe HTTP lifetime and cancellation around the actual provider SSE parser."""

    def __init__(self, source):
        """Capture an iterator without eagerly consuming or interpreting provider chunks."""
        self.source = iter(source)
        self.closed = False
        self.closeCalls = 0

    def __iter__(self):
        """Expose streamed lines lazily so cancellation can stop later output."""
        return self

    def __next__(self):
        """Simulate a transport interrupted by closing its active response."""
        if self.closed:
            raise OSError("transport closed")
        return next(self.source)

    def close(self):
        """Record idempotent response disposal and active execution interruption."""
        self.closeCalls += 1
        self.closed = True

    def __enter__(self):
        """Enter the real adapter's response context."""
        return self

    def __exit__(self, *args):
        """Dispose the response on success, protocol failure, cancellation or close."""
        self.close()


def provider(module=adapter):
    """Create the real adapter over an external driver without any live server."""
    driver = impl.LlamaCppDriver({"baseUrl": "http://127.0.0.1:8080", "contextWindowTokens": 4096})
    return module.LlamaCppStreamProvider(driver=driver)


@pytest.mark.parametrize("options", [
    {"chatTemplateKwargs": "{}"}, {"chatTemplateKwargs": []},
    {"chatTemplateKwargs": {1: "bad"}}, {"chatTemplateKwargs": {"x": float("nan")}},
    {"chatTemplateKwargs": {"x": float("inf")}}, {"chatTemplateKwargs": {"x": object()}},
    {"chatTemplateKwargs": {"enable_thinking": "false"}},
    {"chatTemplateKwargs": {"preserve_reasoning": 1}},
    {"chatTemplateKwargs": {"reasoning_effort": " "}},
    {"reasoningFormat": True}, {"reasoningFormat": "legacy"}, {"reasoningFormat": ""},
    {"serverReasoning": "on"}, {"serverReasoningBudget": 1}, {"reasoningBudget": 5},
    {"reasoningControl": True}, {"reasoning_control": True},
    {"reasoningEffort": "high", "chatTemplateKwargs": {"reasoning_effort": "high"}},
    {"reasoningEffort": "none", "chatTemplateKwargs": {"reasoning_effort": "none"}},
    {"reasoningEffort": "none", "chatTemplateKwargs": {"enable_thinking": True}},
])
def test_request_validation_rejects_types_conflicts_and_out_of_scope_controls(options):
    """Only LC-02 request fields pass, with finite JSON and explicit authority checks."""
    with pytest.raises((TypeError, ValueError)):
        impl._parseInferenceOptions(options)


def test_recursive_kwargs_fail_without_reflecting_private_input():
    """Cycles and private nested keys must not leak into parser diagnostics."""
    value = {}
    value["PRIVATE_SENTINEL"] = value
    with pytest.raises(ValueError) as caught:
        impl._parseInferenceOptions({"chatTemplateKwargs": value})
    assert "PRIVATE_SENTINEL" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("format", ["none", "auto", "deepseek", "deepseek-legacy"])
@pytest.mark.parametrize("module", [adapter, impl])
def test_payload_controls_are_frozen_and_formatting_is_independent(format, module):
    """Valid formatting values, off+effort and nested kwargs are preserved independently."""
    source = {"reasoningEffort": "high", "reasoningFormat": format,
              "chatTemplateKwargs": {"enable_thinking": False, "private": ["before", {"x": 1}]}}
    options = impl._parseInferenceOptions(source)
    source["chatTemplateKwargs"]["private"].append("changed")
    request = LlmCallRequest(query=query(module is adapter))
    payload = module._buildPayload(request, options, includeRequestedModel=False)
    assert payload["reasoning_effort"] == "high"
    assert payload["reasoning_format"] == format
    assert payload["chat_template_kwargs"] == {"enable_thinking": False, "private": ["before", {"x": 1}]}
    payload["chat_template_kwargs"]["private"][1]["x"] = 2
    assert options.chatTemplateKwargs["private"][1]["x"] == 1
    with pytest.raises(TypeError):
        options.chatTemplateKwargs["private"][1]["x"] = 3
    assert "reasoning_format" not in impl._templateControls(impl.LlamaCppOptions())
    assert impl._templateControls(impl.LlamaCppOptions(chatTemplateKwargs={})) == {"chat_template_kwargs": {}}


def test_none_effort_and_template_effort_have_documented_distinct_semantics():
    """OAI none disables thinking, while template 'none' is only a template string."""
    value = impl._parseInferenceOptions({"reasoningEffort": "none", "chatTemplateKwargs": {"enable_thinking": False}})
    assert impl._templateControls(value) == {"reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}}
    value = impl._parseInferenceOptions({"chatTemplateKwargs": {"reasoning_effort": "none", "enable_thinking": True}})
    assert "reasoning_effort" not in impl._templateControls(value)


@pytest.mark.parametrize("module", [adapter, impl])
@pytest.mark.parametrize("model", [None, "target-model"])
def test_estimator_and_inference_use_identical_template_controls_and_routing(monkeypatch, module, model):
    """The exact profile-created estimator renders the same request controls/messages/model."""
    value = provider(module)
    source = {"reasoningEffort": "none", "reasoningFormat": "deepseek",
              "chatTemplateKwargs": {"enable_thinking": False, "private": ["PRIVATE_SENTINEL"]}}
    calls = []

    def post(endpoint, payload, *, timeoutSeconds):
        """Capture real estimator bodies and return a count tied to the exact rendered prompt."""
        calls.append((endpoint, payload))
        if endpoint == "/apply-template":
            assert payload["reasoning_effort"] == "none"
            assert payload["chat_template_kwargs"]["enable_thinking"] is False
            return {"prompt": "rendered-with-controls"}
        assert payload["content"] == "rendered-with-controls"
        return {"tokens": [11, 12, 13]}

    monkeypatch.setattr(value.driver, "postJson", post)
    profile = value.getExecutionProfile(model=model, providerOptions=source)
    source["chatTemplateKwargs"]["private"].append("mutated")
    request = LlmCallRequest(query=query(module is adapter), model=model,
                             providerOptions={"reasoningEffort": "none", "reasoningFormat": "deepseek",
                                              "chatTemplateKwargs": {"enable_thinking": False, "private": ["PRIVATE_SENTINEL"]}})
    payload = module._buildPayload(request, impl._parseInferenceOptions(request.providerOptions), includeRequestedModel=True)
    assert profile.tokenEstimator.estimateInputTokens(request.query) == 3
    assert calls[0][1] == {key: item for key, item in payload.items() if key not in {"stream", "temperature"}}
    assert calls[1][1].get("model") == model
    assert "PRIVATE_SENTINEL" not in repr(profile.metadata)
    assert profile.metadata["tokenEstimation"]["effectiveHonor"] == "unverified-binary-and-template"


@pytest.mark.parametrize("module", [adapter, impl])
@pytest.mark.parametrize("response", [{}, {"prompt": None}, {"prompt": 3}])
def test_estimation_never_returns_count_for_failed_template(monkeypatch, module, response):
    """Incompatible template endpoints fail instead of supplying a misleading fallback count."""
    value = provider(module)
    calls = []

    def post(endpoint, payload, *, timeoutSeconds):
        """Fail template shape and ensure tokenization never happens afterward."""
        calls.append(endpoint)
        return response

    monkeypatch.setattr(value.driver, "postJson", post)
    profile = value.getExecutionProfile(model=None, providerOptions={"reasoningFormat": "auto"})
    with pytest.raises(LlmProviderProtocolError):
        profile.tokenEstimator.estimateInputTokens(query(module is adapter))
    assert calls == ["/apply-template"]


@pytest.mark.parametrize("tokens", [None, {}, [True], ["1"], [-1], [float("nan")]])
def test_malformed_token_ids_do_not_become_exact_counts(tokens):
    """A list length alone does not prove the tokenizer actually returned token IDs."""
    with pytest.raises(LlmProviderProtocolError):
        impl._tokenCount({"tokens": tokens})


def test_sse_reasoning_only_mixed_final_and_native_evidence_are_separate_and_immutable():
    """Preserve exact Unicode reasoning and final native evidence without polluting answer text."""
    events = list(impl._readEvents(lines([
        chunk({"role": "assistant", "reasoning_content": "why é🙂\r\n"}, model="target"),
        chunk({"content": "answer ", "reasoning_content": "then"}),
        chunk({"content": "text"}, finish="stop"),
        {"choices": [], "usage": {"completion_tokens": 12, "completion_tokens_details": {"reasoning_tokens": 9}},
         "timings": {"predicted_n": 12}, "system_fingerprint": "test"},
    ])))
    assert "".join(event.text for event in events) == "answer text"
    assert events[0].text == ""
    assert events[0].metadata["llamaCppReasoning"]["content"] == "why é🙂\r\n"
    assert events[1].metadata["llamaCppReasoning"]["content"] == "then"
    assert events[-1].eventType == "completed"
    assert events[-1].metadata["llamaCppReasoning"]["content"] == "why é🙂\r\nthen"
    assert events[-1].metadata["finish_reason"] == "stop"
    assert events[-1].metadata["usage"]["completion_tokens_details"]["reasoning_tokens"] == 9
    assert events[-1].metadata["timings"]["predicted_n"] == 12
    with pytest.raises(TypeError):
        events[-1].metadata["llamaCppReasoning"]["content"] = "mutated"


@pytest.mark.parametrize("reason", ["", "reasoning only"])
def test_reasoning_only_and_empty_exposure_survive_final_evidence(reason):
    """An empty exposed string is distinguishable from a provider that exposes nothing."""
    events = list(impl._readEvents(lines([chunk({"reasoning_content": reason}, finish="length")])))
    assert events[0].text == ""
    assert events[-1].metadata["llamaCppReasoning"]["content"] == reason
    hidden = list(impl._readEvents(lines([chunk({"content": "inline <think>x</think>"})])))
    assert "llamaCppReasoning" not in hidden[-1].metadata
    assert hidden[0].text == "inline <think>x</think>"


@pytest.mark.parametrize("bad", [
    [], {"error": "PRIVATE_REASONING_SENTINEL"}, {"choices": {}}, {"choices": [0]},
    {"choices": [{"delta": []}]}, {"choices": [{"index": 1, "delta": {}}]},
    {"choices": [{"index": True, "delta": {}}]},
    {"choices": [{"delta": {}}, {"delta": {}}]},
    chunk({"content": []}), chunk({"reasoning_content": []}), chunk({"reasoning_content": False}),
    chunk({}, finish=4), {"choices": [], "usage": {"x": float("nan")}},
])
def test_malformed_stream_chunks_fail_with_no_raw_error_reflection(bad):
    """Invalid provider shapes cannot create successful or private-value diagnostics."""
    with pytest.raises(LlmProviderProtocolError) as caught:
        list(impl._readEvents(lines([bad])))
    assert "PRIVATE_REASONING_SENTINEL" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("source", [
    ["data: not bytes"], [b"data: \xff\n"], [b'data: {"PRIVATE_REASONING_SENTINEL":\n'],
    lines([chunk({"reasoning_content": "partial"})], done=False),
])
def test_malformed_bytes_json_and_truncation_are_protocol_failures(source):
    """Truncation never fabricates completed reasoning evidence or an exact answer."""
    with pytest.raises(LlmProviderProtocolError) as caught:
        list(impl._readEvents(source))
    assert "PRIVATE_REASONING_SENTINEL" not in "".join(traceback.format_exception(caught.value))


def setupProcessing(monkeypatch, source, *, trace=None):
    """Wire the real structured provider through registry, pipeline, transaction and completion."""
    value = provider()
    response = Response(source)
    requests = []

    def openResponse(request, **kwargs):
        """Capture inference HTTP without starting a server or replacing the parser."""
        requests.append(json.loads(request.data))
        return response

    monkeypatch.setattr(adapter.urlRequest, "urlopen", openResponse)
    registry = LlmProviderRegistry()
    scope = RegistrationScope()
    registry.register(scope, ownerId="lc02-test", name="llama.cpp", provider=value)
    scope.publish()
    state = CommittedValueLayer()
    completions = []

    def invoke(capabilityId, payload, memoryView):
        """Build canonical structured input and retain the unchanged complete processing payload."""
        if capabilityId == "items@1":
            return [QueryItem(itemId="question", kind="chat", content="question")]
        if capabilityId == "query@1":
            return query()
        assert capabilityId == "completion@1"
        completions.append(payload)
        return payload["llm"]["providerMetadata"]["llamaCppReasoning"]["content"]

    pipeline = LlmProcessingPipeline(providers=registry, state=state, capabilityInvoker=invoke, trace=trace)
    return pipeline, state, response, requests, completions


def process(pipeline, **kwargs):
    """Exercise the public complete ProcessingRun API including the completion capability."""
    return pipeline.runProcessing(memoryKey="reasoning", inputValue={}, providerName="llama.cpp",
                                  buildQueryItemsCapabilityId="items@1", buildQueryCapabilityId="query@1",
                                  completionCapabilityId="completion@1", **kwargs)


@pytest.mark.parametrize("answer", ["answer only", ""])
def test_complete_processing_result_completion_persistence_and_observation(monkeypatch, answer):
    """Reasoning is retrievable end-to-end while monitoring counts only ordinary answer characters."""
    private = "PRIVATE_REASONING_SENTINEL é🙂"
    trace = []
    pipeline, state, response, requests, completions = setupProcessing(monkeypatch, lines([
        chunk({"reasoning_content": private}), chunk({"content": answer}, finish="stop"),
        {"choices": [], "usage": {"completion_tokens": 10, "completion_tokens_details": {"reasoning_tokens": 8}}},
    ]), trace=lambda *args: trace.append(args))
    observation = StreamObservation()
    observed = []
    result = process(pipeline, streamObserver=observed.append, streamObservation=observation,
                     providerOptions={"reasoningFormat": "deepseek", "chatTemplateKwargs": {"enable_thinking": True}})
    assert result.llm.rawText == answer
    assert result.llm.providerMetadata["llamaCppReasoning"]["content"] == private
    assert result.completionResult == private
    assert completions[0]["llm"]["response"]["rawText"] == answer
    assert completions[0]["llm"]["providerMetadata"]["llamaCppReasoning"]["content"] == private
    persisted = state.load(f"processing/reasoning/runs/{result.processingRunId}")
    assert persisted["response"]["rawText"] == answer
    assert persisted["providerMetadata"]["llamaCppReasoning"]["content"] == private
    assert observed[0].text == "" and observed[0].metadata["llamaCppReasoning"]["content"] == private
    snapshot = observation.snapshot()
    assert snapshot.characterCount == len(answer)
    assert snapshot.deliveryCount == int(bool(answer))
    if not answer:
        assert snapshot.firstTextLatencyNs is None
    assert snapshot.outcome == StreamOutcome.COMPLETED and snapshot.active is False
    assert private not in repr(asdict(snapshot)) and private not in repr(trace)
    assert private not in repr(result.llm.executionProfile.metadata)
    assert response.closed is True
    assert requests[0]["reasoning_format"] == "deepseek"
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
    observation.release()


def test_cancel_after_reasoning_delta_closes_transport_and_rolls_back_processing(monkeypatch):
    """Cancellation of a reasoning-only stream keeps Job outcome, response cleanup and rollback."""
    pipeline, state, response, requests, completions = setupProcessing(monkeypatch, lines([
        chunk({"reasoning_content": "partial private reasoning"}),
        chunk({"content": "must never be accepted"}, finish="stop"),
    ]))
    signal = CancellationSignal()
    observation = StreamObservation()
    seen = []

    def observe(event):
        """Request ordinary Job cancellation after the first preserved reasoning fragment."""
        seen.append(event)
        signal.request()

    with pytest.raises(ExecutionCancelled):
        process(pipeline, cancellationSignal=signal, streamObserver=observe, streamObservation=observation)
    assert len(seen) == 1 and seen[0].text == ""
    assert response.closed and not completions
    assert state.load("processing/reasoning/lastrun") is MISSING
    assert state.load("processing/reasoning/currentqueryitems") is MISSING
    assert observation.snapshot().outcome == StreamOutcome.CANCELLED
    assert observation.snapshot().characterCount == 0
    observation.release()


def test_malformed_reasoning_rolls_back_closes_response_and_marks_observation_failed(monkeypatch):
    """A protocol failure after a reasoning fragment never commits an apparent successful run."""
    pipeline, state, response, requests, completions = setupProcessing(monkeypatch, lines([
        chunk({"reasoning_content": "partial"}), chunk({"reasoning_content": {"invalid": True}}),
    ]))
    observation = StreamObservation()
    with pytest.raises(LlmProviderProtocolError):
        process(pipeline, streamObservation=observation)
    assert response.closed and not completions
    assert state.load("processing/reasoning/lastrun") is MISSING
    assert observation.snapshot().outcome == StreamOutcome.FAILED
    observation.release()


def test_template_transport_failure_is_not_silently_estimated(monkeypatch):
    """An unsupported endpoint/setting produces a clear failure rather than a count for other controls."""
    value = provider()

    def fail(*args, **kwargs):
        """Model an external endpoint that cannot handle requested template settings."""
        raise LlmProviderConnectionError("template endpoint unavailable")

    monkeypatch.setattr(value.driver, "postJson", fail)
    profile = value.getExecutionProfile(model=None, providerOptions={"chatTemplateKwargs": {"enable_thinking": False}})
    with pytest.raises(LlmProviderConnectionError):
        profile.tokenEstimator.estimateInputTokens(query())


def test_managed_request_overrides_defaults_without_mutating_launch_or_model_routing(monkeypatch, tmp_path):
    """Per-request controls override server defaults only in payload, preserving LC-01 identity/argv."""
    executable = tmp_path / "server.exe"
    target = tmp_path / "target.gguf"
    executable.write_bytes(b"placeholder")
    target.write_bytes(b"placeholder")
    value = adapter.LlamaCppDriver({"manageServer": True, "executable": str(executable),
        "models": {"target": {"modelPath": str(target), "serverReasoning": "off",
                              "chatTemplateKwargs": {"private_default": "unchanged"}}}})
    model = value.models["target"]
    argv = value._serverArgs(model)
    evidence = value.configurationEvidence()
    selections = []

    def select(name):
        """Represent compatible already resident model selection without starting a process."""
        selections.append(name)
        return model

    calls = []

    def post(endpoint, payload, *, timeoutSeconds):
        """Capture template/tokenizer routing to the managed resident target."""
        calls.append(payload)
        return {"prompt": "rendered"} if endpoint == "/apply-template" else {"tokens": []}

    monkeypatch.setattr(value, "ensureModel", select)
    monkeypatch.setattr(value, "postJson", post)
    response = Response(lines([chunk({"reasoning_content": "new private output"})]))
    requests = []

    def openResponse(request, **kwargs):
        """Observe actual request JSON while keeping startup resources untouched."""
        requests.append(json.loads(request.data))
        return response

    monkeypatch.setattr(adapter.urlRequest, "urlopen", openResponse)
    options = {"reasoningFormat": "deepseek", "chatTemplateKwargs": {"enable_thinking": True}}
    active = adapter.LlamaCppStreamProvider(driver=value)
    profile = active.getExecutionProfile(model="target", providerOptions=options)
    assert profile.tokenEstimator.estimateInputTokens(query()) == 0
    list(active.stream(LlmCallRequest(query=query(), model="target", providerOptions=options)))
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert "model" not in requests[0] and all("model" not in item for item in calls)
    assert value._serverArgs(model) == argv
    assert value.configurationEvidence() == evidence
    assert selections == ["target", "target"]


def test_invalid_request_fails_before_model_selection(monkeypatch):
    """Reject malformed request controls before they can materialize managed residency."""
    value = provider()

    def forbidden(*args, **kwargs):
        """Fail if validation is delayed until after residency selection."""
        raise AssertionError("must not select/start a model")

    monkeypatch.setattr(value.driver, "ensureModel", forbidden)
    with pytest.raises(ValueError):
        value.getExecutionProfile(model=None, providerOptions={"reasoningFormat": "invalid"})
    with pytest.raises(ValueError):
        list(value.stream(LlmCallRequest(query=query(), providerOptions={"reasoningFormat": "invalid"})))


def test_consumer_closing_after_reasoning_fragment_closes_http_response(monkeypatch):
    """Early consumer close disposes the actual response and partial reasoning generator."""
    value = provider()
    response = Response(lines([chunk({"reasoning_content": "partial"}), chunk({"content": "later"})]))

    def openResponse(*args, **kwargs):
        """Return a context-managed response consumed by the real adapter parser."""
        return response

    monkeypatch.setattr(adapter.urlRequest, "urlopen", openResponse)
    stream = value.stream(LlmCallRequest(query=query()))
    assert next(stream).metadata["llamaCppReasoning"]["content"] == "partial"
    stream.close()
    assert response.closed
