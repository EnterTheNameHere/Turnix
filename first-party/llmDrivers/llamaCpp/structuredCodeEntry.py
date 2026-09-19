# file: first-party/llmDrivers/llamaCpp/structuredCodeEntry.py ; version: 7
# ruff: noqa: INP001
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request as urlRequest
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
from backend.runtime.sharedServices import sharedServicesForApplicationRun

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from types import ModuleType

    from backend.context.codeEntryContext import CodeEntryContext
    from backend.core.immutableValue import ImmutableValue
    from backend.runtime.sharedServices import SharedServiceLease


_IMPLEMENTATION_NAME = "actantFirstPartyLlamaCppImplementation"
_IMPLEMENTATION_PATH = Path(__file__).with_name("codeEntry.py")
_MANAGED_SERVICE_ID = "llm.driver.llama.cpp.managed"


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
    """Tracks one application provider facade and its host/shared driver claim."""

    driver: object
    sharedLease: SharedServiceLease[object] | None


def _managedCompatibilityKey(config: Mapping[str, object]) -> str:
    """Returns deterministic configuration identity for one managed host driver."""
    try:
        return json.dumps(
            dict(config),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as err:
        raise ValueError(
            "Managed llama.cpp configuration must be JSON-compatible for host sharing.",
        ) from err


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
    """Exact token estimator for plain and structured Actant chat queries."""

    def __init__(self, *, driver: LlamaCppDriver, timeoutSeconds: float) -> None:
        """Binds estimation to one driver and request timeout."""
        self._driver = driver
        self._timeoutSeconds = timeoutSeconds

    def estimateInputTokens(self, query: LlmQuery) -> int:
        """Counts tokens after templating the same exact messages used for inference."""
        messages = _messagesForQuery(query)
        templated = self._driver.postJson(
            "/apply-template",
            {"messages": messages},
            timeoutSeconds=self._timeoutSeconds,
        )
        prompt = templated.get("prompt")
        if type(prompt) is not str:
            raise _impl.LlmProviderProtocolError(
                "llama.cpp /apply-template response does not contain a string prompt.",
            )
        tokenized = self._driver.postJson(
            "/tokenize",
            {
                "content": prompt,
                "add_special": False,
                "parse_special": True,
                "with_pieces": False,
            },
            timeoutSeconds=self._timeoutSeconds,
        )
        tokens = tokenized.get("tokens")
        if not isinstance(tokens, list):
            raise _impl.LlmProviderProtocolError(
                "llama.cpp /tokenize response does not contain a tokens list.",
            )
        return len(tokens)


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
            "baseUrl": self.driver.baseUrl,
            "timeoutSeconds": options.timeoutSeconds,
            "managedServer": self.driver.manageServer,
        }
        if selected is not None:
            metadata["activeModel"] = selected.name
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
            ),
            metadata=metadata,
        )

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        """Streams one query without flattening structured conversation turns."""
        selected = self.driver.ensureModel(request.model)
        options = _impl._parseInferenceOptions(request.providerOptions)  # noqa: SLF001 - adapter reuses substrate parser.
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
        try:
            with urlRequest.urlopen(  # noqa: S310 - provider endpoint is explicitly configured by Actant.
                httpRequest,
                timeout=options.timeoutSeconds,
            ) as response:
                yield from _impl._readEvents(response)  # noqa: SLF001 - adapter reuses substrate SSE parser.
        except HTTPError as err:
            active = "" if selected is None else f" for model {selected.name!r}"
            raise LlmProviderConnectionError(
                f"llama.cpp returned HTTP {err.code} for {endpoint}{active}.",
            ) from err
        except (URLError, TimeoutError) as err:
            raise LlmProviderConnectionError(
                f"Failed communicating with llama.cpp at {endpoint}.",
            ) from err


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
        ("reasoning_effort", options.reasoningEffort),
    )
    payload.update({key: value for key, value in mappings if value is not None})
    return payload


def onLoad(ctx: CodeEntryContext) -> _LlamaCppRuntimeState:
    """Publishes one application provider facade over local or host-shared driver state."""
    config = ctx.config.get("llamaCpp", {})
    if not isinstance(config, dict):
        raise TypeError("llamaCpp configuration must be an object.")

    manageServer = config.get("manageServer", False)
    if type(manageServer) is not bool:
        raise TypeError("llamaCpp.manageServer must be a boolean.")

    lease: SharedServiceLease[object] | None = None
    if manageServer:
        registry = sharedServicesForApplicationRun(ctx.identity.applicationRunId)
        lease = registry.acquire(
            serviceId=_MANAGED_SERVICE_ID,
            compatibilityKey=_managedCompatibilityKey(config),
            factory=lambda: LlamaCppDriver(config),
            closer=lambda resource: resource.stop(),
        )
        driver = lease.resource
    else:
        driver = LlamaCppDriver(config)

    try:
        ctx.llm.registerProvider(
            "llama.cpp",
            LlamaCppStreamProvider(driver=driver),
        )
    except Exception:
        if lease is None:
            driver.stop()
        else:
            lease.release()
        raise
    return _LlamaCppRuntimeState(driver=driver, sharedLease=lease)


def onUnload(ctx: CodeEntryContext, state: object) -> None:
    """Releases this application's provider claim without owning another application's resource."""
    del ctx
    if not isinstance(state, _LlamaCppRuntimeState):
        return
    if state.sharedLease is not None:
        state.sharedLease.release()
        return
    if isinstance(state.driver, LlamaCppDriver):
        state.driver.stop()
