# file: first-party/llmDrivers/llamaCpp/llamaCppProvider.py ; version: 4
from __future__ import annotations

import json
import urllib.request as urlRequest
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.error import HTTPError, URLError

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import (
    requireExactNonBlankString,
    requireFloat,
    requireMapping,
    requireOptionalPositiveInteger,
    requirePositiveFloat,
    typeName,
)
from backend.llm.errors import LlmProviderConnectionError, LlmProviderProtocolError
from backend.llm.llmTypes import (
    LlmCallRequest,
    LlmExecutionProfile,
    LlmStreamEvent,
    LlmStreamProvider,
)

__all__: list[str] = [
    "LlamaCppProviderOptions",
    "LlamaCppStreamProvider",
    "buildRequestPayload",
    "extractStreamChunk",
    "parseProviderOptions",
    "readStreamEvents",
    "requireBaseUrl",
]


DEFAULT_BASE_URL: Final[str] = "http://localhost:8080"
DEFAULT_TIMEOUT_SECONDS: Final[float] = 120.0
DEFAULT_TEMPERATURE: Final[float] = 0.7
DEFAULT_MODEL: Final[str] = "local-model"
_LLAMA_CPP_PROVIDER_OPTION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "baseUrl",
        "contextWindowTokens",
        "maxTokens",
        "temperature",
        "timeoutSeconds",
    },
)


class LlamaCppStreamProvider(LlmStreamProvider):
    """Streams completions from an OpenAI-compatible llama.cpp server."""

    def __init__(
        self,
        *,
        baseUrl: str = DEFAULT_BASE_URL,
        timeoutSeconds: float = DEFAULT_TIMEOUT_SECONDS,
        contextWindowTokens: int | None = None,
    ) -> None:
        """Initializes the llama.cpp stream provider."""
        self.baseUrl = requireBaseUrl(baseUrl, "baseUrl")
        self.timeoutSeconds = requirePositiveFloat(
            timeoutSeconds,
            "timeoutSeconds",
        )
        self.contextWindowTokens = requireOptionalPositiveInteger(
            contextWindowTokens,
            "contextWindowTokens",
        )

    def getExecutionProfile(
        self,
        *,
        model: str | None,
        providerOptions: Mapping[str, ImmutableValue],
    ) -> LlmExecutionProfile:
        """Returns the execution profile for the llama.cpp server."""
        del model
        options = self._parseOptions(providerOptions)
        return LlmExecutionProfile(
            contextWindowTokens=options.contextWindowTokens,
            tokenEstimator=None,
            metadata={"baseUrl": options.baseUrl},
        )

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        """Streams completion events for one LLM call request."""
        if not isinstance(request, LlmCallRequest):
            raise TypeError(
                "request must be an LlmCallRequest; "
                f"got {typeName(request)}.",
            )

        options = self._parseOptions(request.providerOptions)
        endpoint = f"{options.baseUrl}/v1/chat/completions"
        payload = buildRequestPayload(
            request=request,
            options=options,
            defaultModel=DEFAULT_MODEL,
        )

        httpRequest = urlRequest.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlRequest.urlopen(
                httpRequest,
                timeout=options.timeoutSeconds,
            ) as response:
                yield from readStreamEvents(response)
        except HTTPError as err:
            raise LlmProviderConnectionError(
                f"llama.cpp server returned HTTP {err.code} for {endpoint}.",
            ) from err
        except URLError as err:
            raise LlmProviderConnectionError(
                f"Failed to connect to llama.cpp server at {endpoint}.",
            ) from err
        except TimeoutError as err:
            raise LlmProviderConnectionError(
                f"Timed out while communicating with "
                f"llama.cpp server at {endpoint}.",
            ) from err

    def _parseOptions(
        self,
        providerOptions: Mapping[str, ImmutableValue],
    ) -> LlamaCppProviderOptions:
        """Parses provider options using the configured defaults."""
        return parseProviderOptions(
            providerOptions=providerOptions,
            defaultBaseUrl=self.baseUrl,
            defaultTimeoutSeconds=self.timeoutSeconds,
            defaultContextWindowTokens=self.contextWindowTokens,
            defaultTemperature=DEFAULT_TEMPERATURE,
        )


class ByteLineStream(Protocol):
    """Provides iteration over lines represented as bytes."""

    def __iter__(self) -> Iterator[bytes]:
        """Returns an iterator over the stream's lines."""
        ...


@dataclass(frozen=True, slots=True)
class LlamaCppProviderOptions:
    """Validated llama.cpp provider options."""

    baseUrl: str
    timeoutSeconds: float
    temperature: float
    maxTokens: int | None
    contextWindowTokens: int | None

    def __post_init__(self) -> None:
        """Validates and normalizes the llama.cpp provider options."""
        object.__setattr__(
            self,
            "baseUrl",
            requireBaseUrl(self.baseUrl, "baseUrl"),
        )
        object.__setattr__(
            self,
            "timeoutSeconds",
            requirePositiveFloat(
                self.timeoutSeconds,
                "timeoutSeconds",
            ),
        )

        temperature = requireFloat(
            self.temperature,
            "temperature",
        )
        if temperature < 0:
            raise ValueError(
                "temperature must not be negative; "
                f"got {temperature}.",
            )
        object.__setattr__(self, "temperature", temperature)
        object.__setattr__(
            self,
            "maxTokens",
            requireOptionalPositiveInteger(
                self.maxTokens,
                "maxTokens",
            ),
        )
        object.__setattr__(
            self,
            "contextWindowTokens",
            requireOptionalPositiveInteger(
                self.contextWindowTokens,
                "contextWindowTokens",
            ),
        )


def parseProviderOptions(
    providerOptions: Mapping[str, ImmutableValue],
    *,
    defaultBaseUrl: str,
    defaultTimeoutSeconds: float,
    defaultContextWindowTokens: int | None,
    defaultTemperature: float,
) -> LlamaCppProviderOptions:
    """Parses and validates llama.cpp provider options."""
    requireMapping(providerOptions, "providerOptions")

    for key in providerOptions:
        if not isinstance(key, str):
            raise TypeError(
                "providerOptions keys must be strings; "
                f"got key {key!r} of type {typeName(key)}.",
            )

    unknownKeys = providerOptions.keys() - _LLAMA_CPP_PROVIDER_OPTION_KEYS

    if unknownKeys:
        unknownKey = min(unknownKeys)
        raise ValueError(
            "providerOptions contains an unsupported llama.cpp option; "
            f"key={unknownKey!r}.",
        )

    baseUrl = requireBaseUrl(
        providerOptions.get("baseUrl", defaultBaseUrl),
        "providerOptions.baseUrl",
    )
    timeoutSeconds = requirePositiveFloat(
        providerOptions.get("timeoutSeconds", defaultTimeoutSeconds),
        "providerOptions.timeoutSeconds",
    )
    temperature = requireFloat(
        providerOptions.get("temperature", defaultTemperature),
        "providerOptions.temperature",
    )
    if temperature < 0:
        raise ValueError("providerOptions.temperature must not be negative.")

    maxTokens = requireOptionalPositiveInteger(
        providerOptions.get("maxTokens"),
        "providerOptions.maxTokens",
    )
    contextWindowTokens = requireOptionalPositiveInteger(
        providerOptions.get("contextWindowTokens", defaultContextWindowTokens),
        "providerOptions.contextWindowTokens",
    )

    return LlamaCppProviderOptions(
        baseUrl=baseUrl,
        timeoutSeconds=timeoutSeconds,
        temperature=temperature,
        maxTokens=maxTokens,
        contextWindowTokens=contextWindowTokens,
    )


def buildRequestPayload(
    request: LlmCallRequest,
    options: LlamaCppProviderOptions,
    *,
    defaultModel: str,
) -> dict[str, object]:
    """Builds one OpenAI-compatible chat-completion payload."""
    if not isinstance(request, LlmCallRequest):
        raise TypeError(
            "request must be an LlmCallRequest; "
            f"got {typeName(request)}.",
        )
    if not isinstance(options, LlamaCppProviderOptions):
        raise TypeError(
            "options must be a LlamaCppProviderOptions; "
            f"got {typeName(options)}.",
        )

    cleanDefaultModel = requireExactNonBlankString(defaultModel, "defaultModel")

    payload: dict[str, object] = {
        "model": request.model or cleanDefaultModel,
        "messages": [
            {"role": "user", "content": request.prompt},
        ],
        "temperature": options.temperature,
        "stream": True,
    }

    if options.maxTokens is not None:
        payload["max_tokens"] = options.maxTokens

    return payload


def readStreamEvents(response: ByteLineStream) -> Iterator[LlmStreamEvent]:
    """Parses llama.cpp Server-Sent Events from a byte-line stream."""
    finalMetadata: dict[str, ImmutableValue] = {}

    for rawLine in response:
        if not isinstance(rawLine, bytes):
            raise LlmProviderProtocolError(
                "llama.cpp stream produced a non-bytes line.",
            )

        line = rawLine.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue

        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            yield LlmStreamEvent(
                eventType="completed",
                metadata=finalMetadata,
            )
            return

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as err:
            raise LlmProviderProtocolError(
                "llama.cpp stream contained invalid JSON.",
            ) from err

        text, metadata = extractStreamChunk(chunk)
        finalMetadata.update(metadata)
        if text or metadata:
            yield LlmStreamEvent(
                eventType="delta",
                text=text,
                metadata=metadata,
            )

    # Natural EOF is not promoted to completion. The pipeline rejects the
    # missing completed event.


def requireBaseUrl(value: object, name: str) -> str:
    """Validates and normalizes one llama.cpp server base URL."""
    cleanName = requireExactNonBlankString(name, "name")
    cleanBaseUrl = requireExactNonBlankString(value, cleanName).rstrip("/")

    if not cleanBaseUrl:
        raise ValueError(f"{cleanName} must contain characters other than '/'.")
    return cleanBaseUrl


def extractStreamChunk(
    chunk: Mapping[str, object],
) -> tuple[str, Mapping[str, ImmutableValue]]:
    """Extract delta text and supported metadata from one stream chunk."""
    if not isinstance(chunk, Mapping):
        raise LlmProviderProtocolError(
            "llama.cpp stream chunk must be a Mapping.",
        )

    metadataSource: dict[str, object] = {}
    for key in ("model", "usage", "timings", "system_fingerprint"):
        if key in chunk:
            metadataSource[key] = chunk[key]

    metadata = ImmutableValueFreezer().freezeMapping(
        metadataSource,
        "llamaCppStreamMetadata",
    )

    choices = chunk.get("choices")
    if not isinstance(choices, list):
        raise LlmProviderProtocolError(
            "llama.cpp stream chunk must contain a 'choices' list.",
        )
    if not choices:
        return "", metadata

    firstChoice = choices[0]
    if not isinstance(firstChoice, Mapping):
        raise LlmProviderProtocolError(
            "llama.cpp stream 'choice' must be a Mapping.",
        )

    delta = firstChoice.get("delta")
    if delta is None:
        return "", metadata
    if not isinstance(delta, Mapping):
        raise LlmProviderProtocolError(
            "llama.cpp stream delta must be a Mapping.",
        )

    content = delta.get("content")
    if content is None:
        return "", metadata
    if not isinstance(content, str):
        raise LlmProviderProtocolError(
            "llama.cpp stream delta content must be a string.",
        )

    return content, metadata
