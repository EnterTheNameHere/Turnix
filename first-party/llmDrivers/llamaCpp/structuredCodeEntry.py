# file: first-party/llmDrivers/llamaCpp/structuredCodeEntry.py ; version: 11
# ruff: noqa: INP001
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request as urlRequest
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError

from backend.llm.errors import LlmProviderConnectionError
from backend.llm.llmTypes import (
    LlmCallRequest,
    LlmExecutionProfile,
    LlmQuery,
    LlmStreamEvent,
)
from backend.llm.structuredMessages import (
    LLM_MESSAGES_FORMAT_ID,
    LlmMessages,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from types import ModuleType

    from backend.context.codeEntryContext import CodeEntryContext
    from backend.core.immutableValue import ImmutableValue


_IMPLEMENTATION_NAME = "actantFirstPartyLlamaCppImplementation"
_IMPLEMENTATION_PATH = Path(__file__).with_name("codeEntry.py")


def _loadImplementation() -> ModuleType:
    """Loads the existing llama.cpp driver implementation as this adapter's substrate."""
    existing = sys.modules.get(_IMPLEMENTATION_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        _IMPLEMENTATION_NAME,
        _IMPLEMENTATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to construct the llama.cpp implementation module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_IMPLEMENTATION_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_IMPLEMENTATION_NAME, None)
        raise
    return module


_impl = _loadImplementation()
LlamaCppDriver = _impl.LlamaCppDriver
LlamaCppModel = _impl.LlamaCppModel
LlamaCppOptions = _impl.LlamaCppOptions


@dataclass(slots=True)
class _LlamaCppRuntimeState:
    """Own the local external facade only; managed bindings own their own lifetime."""

    driver: object


def _messagesForQuery(query: LlmQuery) -> list[dict[str, str]]:
    """Maps one supported Actant query to exact llama.cpp chat messages."""
    if query.formatId == "text/plain":
        if type(query.payload) is not str:
            raise TypeError("text/plain LlmQuery payload must be an exact built-in string.")
        return [{"role": "user", "content": query.payload}]
    if query.formatId == LLM_MESSAGES_FORMAT_ID:
        return LlmMessages.fromPayload(query.payload).snapshot()
    raise ValueError(
        "llama.cpp supports text/plain and actant.llm.messages@1, not "
        f"{query.formatId!r}.",
    )


class LlamaCppTokenEstimator:
    """Count server-rendered structured prompts with inference-equivalent template controls."""

    def __init__(self, *, driver: LlamaCppDriver, timeoutSeconds: float,
                 options: LlamaCppOptions | None = None, model: str | None = None) -> None:
        """Bind immutable template controls, timeout and external model routing."""
        self._driver = driver
        self._timeoutSeconds = timeoutSeconds
        controls = _impl._templateControls(LlamaCppOptions() if options is None else options)
        if not driver.manageServer and model is not None:
            controls["model"] = model
        self._controls = _impl.ImmutableValueFreezer().freezeMapping(controls)

    def estimateInputTokens(self, query: LlmQuery) -> int:
        """Count the returned prompt under matching controls; endpoint failure has no fallback."""
        messages = _messagesForQuery(query)
        templated = self._driver.postJson(
            "/apply-template",
            {"messages": messages, **_impl._configuration.jsonValue(self._controls)},
            timeoutSeconds=self._timeoutSeconds,
        )
        prompt = templated.get("prompt")
        if type(prompt) is not str:
            raise _impl.LlmProviderProtocolError(
                "llama.cpp /apply-template response does not contain a string prompt.",
            )
        tokenized = self._driver.postJson(
            "/tokenize",
            _impl._tokenizePayload(prompt, self._controls),
            timeoutSeconds=self._timeoutSeconds,
        )
        return _impl._tokenCount(tokenized)


class LlamaCppStreamProvider:
    """llama.cpp provider supporting plain text and structured message queries."""

    def __init__(self, *, driver: LlamaCppDriver) -> None:
        """Binds this application-local provider facade to one driver resource."""
        self.driver = driver

    def getExecutionProfile(
        self,
        *,
        model: str | None,
        providerOptions: Mapping[str, ImmutableValue],
    ) -> LlmExecutionProfile:
        """Returns execution evidence and a query-format-aware exact estimator."""
        options = _impl._parseInferenceOptions(providerOptions)  # noqa: SLF001 - adapter reuses substrate parser.
        selected = self.driver.ensureModel(model)
        metadata: dict[str, ImmutableValue] = {
            "timeoutSeconds": options.timeoutSeconds,
            "managedServer": self.driver.manageServer,
            "configurationEvidence": self.driver.configurationSummary(model),
            "tokenEstimation": _impl._estimationMetadata(),
        }
        if selected is not None:
            metadata["launchProfile"] = selected.launchProfile()
        return LlmExecutionProfile(
            contextWindowTokens=(
                selected.contextWindowTokens
                if selected is not None
                else self.driver.externalContextWindowTokens
            ),
            tokenEstimator=LlamaCppTokenEstimator(
                driver=self.driver,
                timeoutSeconds=options.timeoutSeconds,
                options=options,
                model=model,
            ),
            metadata=metadata,
        )

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        """
        Streams one query and registers provider-specific execution-stop behavior.

        The Job-owned cancellation signal remains provider-neutral. This adapter
        realizes an active stop request by closing the current HTTP response,
        while the generic pipeline owns cancellation lifecycle classification.
        """
        cancellationSignal = request.cancellationSignal
        if cancellationSignal is not None:
            cancellationSignal.raiseIfRequested()

        options = _impl._parseInferenceOptions(request.providerOptions)
        self.driver.ensureModel(request.model)
        if cancellationSignal is not None:
            cancellationSignal.raiseIfRequested()

        payload = _buildPayload(
            request,
            options,
            includeRequestedModel=not self.driver.manageServer,
        )
        endpoint = f"{self.driver.baseUrl}/v1/chat/completions"
        httpRequest = urlRequest.Request(  # noqa: S310 - driver configuration controls the explicit provider endpoint.
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        unregisterCancellation = None
        try:
            response = urlRequest.urlopen(  # noqa: S310 - provider endpoint is explicitly configured by Actant.
                httpRequest,
                timeout=options.timeoutSeconds,
            )

            def stopResponse() -> None:
                """Best-effort closes the active provider response on cancellation."""
                with suppress(Exception):
                    response.close()

            if cancellationSignal is not None:
                unregisterCancellation = cancellationSignal.registerCallback(stopResponse)
                cancellationSignal.raiseIfRequested()

            with response:
                yield from _impl._readEvents(response)  # noqa: SLF001 - adapter reuses substrate SSE parser.
        except HTTPError as err:
            raise LlmProviderConnectionError(
                f"llama.cpp returned HTTP {err.code} for chat completions.",
            ) from None
        except (URLError, TimeoutError):
            raise LlmProviderConnectionError(
                "Failed communicating with llama.cpp for chat completions.",
            ) from None
        finally:
            if unregisterCancellation is not None:
                unregisterCancellation()


def _buildPayload(
    request: LlmCallRequest,
    options: LlamaCppOptions,
    *,
    includeRequestedModel: bool,
) -> dict[str, object]:
    """Builds an OpenAI-compatible payload from the exact Actant query messages."""
    payload: dict[str, object] = {
        "messages": _messagesForQuery(request.query),
        "stream": True,
        "temperature": options.temperature,
    }
    if includeRequestedModel and request.model is not None:
        payload["model"] = request.model
    mappings = (
        ("max_tokens", options.maxTokens),
        ("top_p", options.topP),
        ("top_k", options.topK),
        ("min_p", options.minP),
        ("repeat_penalty", options.repeatPenalty),
        ("seed", options.seed),
    )
    payload.update({key: value for key, value in mappings if value is not None})
    payload.update(_impl._templateControls(options))
    return payload


def onLoad(ctx: CodeEntryContext) -> object:
    """Publishes one application provider facade over local or host-shared driver state."""
    config = ctx.config.get("llamaCpp", {})
    if not isinstance(config, dict):
        raise TypeError("llamaCpp configuration must be an object.")

    manageServer = config.get("manageServer", False)
    if type(manageServer) is not bool:
        raise TypeError("llamaCpp.manageServer must be a boolean.")

    if manageServer:
        facade = _impl._ManagedFacade(config, structured=True)
        ctx.llm.registerProvider("llama.cpp", facade)
        return facade
    driver = LlamaCppDriver(config)

    try:
        ctx.llm.registerProvider(
            "llama.cpp",
            LlamaCppStreamProvider(driver=driver),
        )
    except Exception:
        driver.stop()
        raise
    return _LlamaCppRuntimeState(driver=driver)


def onUnload(ctx: CodeEntryContext, state: object) -> None:
    """Releases this application's provider claim without owning another application's resource."""
    del ctx
    if not isinstance(state, _LlamaCppRuntimeState):
        return
    if isinstance(state.driver, LlamaCppDriver):
        state.driver.stop()
