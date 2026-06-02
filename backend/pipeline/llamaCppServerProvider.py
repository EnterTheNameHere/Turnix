# backend/pipeline/llamaCppServerProvider.py
from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.pipeline.modelProvider import ModelResponse, ModelTimings, ModelUsage


class LlamaCppServerProviderError(RuntimeError):
    """Raised when llama.cpp server communication or response parsing fails."""

    def __init__(
        self,
        message: str,
        *,
        statusCode: int | None = None,
        errorType: str | None = None,
        serverMessage: str | None = None,
        providerDetails: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.statusCode = statusCode
        self.errorType = errorType
        self.serverMessage = serverMessage
        self.providerDetails = providerDetails or {}


class LlamaCppContextExceededError(LlamaCppServerProviderError):
    """Raised when llama.cpp rejects a request because it exceeds context size."""

    def __init__(
        self,
        message: str,
        *,
        promptTokensCount: int | None = None,
        contextSize: int | None = None,
        statusCode: int | None = None,
        serverMessage: str | None = None,
        providerDetails: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            statusCode=statusCode,
            errorType="exceed_context_size_error",
            serverMessage=serverMessage,
            providerDetails=providerDetails,
        )
        self.promptTokensCount = promptTokensCount
        self.contextSize = contextSize


@dataclass(frozen=True)
class LlamaCppServerConfig:
    """Configuration for llama.cpp server chat completion."""

    baseUrl: str = "http://127.0.0.1:1234"
    model: str = ""
    temperature: float = 0.6
    maxTokens: int = 512
    timeoutSeconds: float = 120.0


@dataclass(frozen=True)
class JsonPostResult:
    """Validated JSON response plus local request timing."""

    responseJson: dict[str, Any]
    wallMilliseconds: float


class LlamaCppServerProvider:
    """Synchronous llama.cpp chat provider."""

    def __init__(self, config: LlamaCppServerConfig | None = None) -> None:
        self.config = config or LlamaCppServerConfig()

    def generateChatResponse(self, messages: list[dict[str, Any]]) -> ModelResponse:
        if not messages:
            raise LlamaCppServerProviderError("Cannot generate chat response without messages")
        
        payload = {
            "messages": self._normalizeMessages(messages),
            "temperature": self.config.temperature,
            "max_tokens": self.config.maxTokens,
            "stream": False,
        }
        if self.config.model:
            payload["model"] = self.config.model

        postResult = self._postJson("/v1/chat/completions", payload)
        return self._parseModelResponse(postResult.responseJson, postResult.wallMilliseconds)
    
    def _postJson(self, path: str, payload: dict[str, Any]) -> JsonPostResult:
        url = self.config.baseUrl.rstrip("/") + path
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        
        startedAt = perf_counter()
        try:
            with urlopen(request, timeout=self.config.timeoutSeconds) as response:
                responseBody = response.read().decode("utf-8", errors="replace")
        except HTTPError as err:
            errorBody = err.read().decode("utf-8", errors="replace")
            self._raiseHttpError(err, errorBody)
        except TimeoutError as err:
            raise LlamaCppServerProviderError(
                f"llama.cpp request timed out after {self.config.timeoutSeconds} seconds",
            ) from err
        except URLError as err:
            raise LlamaCppServerProviderError(
                f"llama.cpp server is not reachable at {self.config.baseUrl}: {err.reason}",
            ) from err
        finally:
            wallMilliseconds = (perf_counter() - startedAt) * 1000.0

        try:
            parsed = json.loads(responseBody)
        except json.JSONDecodeError as err:
            errorPreview = self._previewAroundPosition(responseBody, err.pos)
            
            raise LlamaCppServerProviderError(
                f"llama.cpp response is not valid JSON: {err.msg} at position {err.pos}\n{errorPreview}",
            ) from err

        if not isinstance(parsed, dict):
            raise LlamaCppServerProviderError("llama.cpp returned JSON that is not an object")
        
        return JsonPostResult(responseJson=parsed, wallMilliseconds=wallMilliseconds)
    
    def _raiseHttpError(self, err: HTTPError, errorBody: str) -> None:
        parsedError = self._parseErrorBody(errorBody)
        if parsedError is None:
            errorPreview = self._previewFromStart(errorBody)
            raise LlamaCppServerProviderError(
                f"llama.cpp HTTP error {err.code}: {errorPreview}",
                statusCode=err.code,
            ) from err

        errorObject = parsedError.get("error")
        if not isinstance(errorObject, dict):
            errorPreview = self._previewFromStart(errorBody)
            raise LlamaCppServerProviderError(
                f"llama.cpp HTTP error {err.code}: {errorPreview}",
                statusCode=err.code,
                providerDetails=parsedError,
            ) from err

        errorType = self._extractOptionalString(errorObject, "type")
        serverMessage = self._extractOptionalString(errorObject, "message")
        serverCode = self._extractOptionalInt(errorObject, "code")
        statusCode = serverCode if serverCode is not None else err.code

        if errorType == "exceed_context_size_error":
            promptTokensCount = self._extractOptionalInt(errorObject, "n_prompt_tokens")
            contextSize = self._extractOptionalInt(errorObject, "n_ctx")
            raise LlamaCppContextExceededError(
                self._makeContextExceededMessage(promptTokensCount, contextSize, serverMessage),
                promptTokensCount=promptTokensCount,
                contextSize=contextSize,
                statusCode=statusCode,
                serverMessage=serverMessage,
                providerDetails=errorObject,
            ) from err
        
        message = serverMessage or self._previewFromStart(errorBody)
        raise LlamaCppServerProviderError(
            f"llama.cpp {errorType or 'http_error'} {statusCode}: {message}",
            statusCode=statusCode,
            errorType=errorType,
            serverMessage=serverMessage,
            providerDetails=errorObject,
        ) from err
    
    def _parseErrorBody(self, errorBody: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(errorBody)
        except json.JSONDecodeError:
            return None

        if isinstance(parsed, dict):
            return parsed
        
        return None
    
    def _makeContextExceededMessage(
        self,
        promptTokensCount: int | None,
        contextSize: int | None,
        serverMessage: str | None,
    ) -> str:
        if promptTokensCount is not None and contextSize is not None:
            return (
                f"llama.cpp context exceeded: request used {promptTokensCount} prompt tokens, "
                f"context size is {contextSize}."
            )
        
        if serverMessage:
            return f"llama.cpp context exceeded: {serverMessage}"
        
        return "llama.cpp context exceeded."
    
    def _parseModelResponse(self, responseJson: dict[str, Any], wallMilliseconds: float) -> ModelResponse:
        choices = responseJson.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlamaCppServerProviderError("llama.cpp returned JSON without a non-empty choices field")
        
        firstChoice = choices[0]
        if not isinstance(firstChoice, dict):
            raise LlamaCppServerProviderError("llama.cpp first choice is not an object")
        
        finishReason = self._extractOptionalString(firstChoice, "finish_reason")
        content, reasoningContent = self._extractMessageContent(firstChoice)

        return ModelResponse(
            content=content,
            finishReason=finishReason,
            reasoningContent=reasoningContent,
            model=self._extractOptionalString(responseJson, "model"),
            usage=self._extractUsage(responseJson),
            timings=self._extractTimings(responseJson, wallMilliseconds),
            providerDetails={
                "id": self._extractOptionalString(responseJson, "id"),
                "object": self._extractOptionalString(responseJson, "object"),
                "created": self._extractOptionalInt(responseJson, "created"),
                "systemFingerprint": self._extractOptionalString(responseJson, "system_fingerprint"),
            },
        )
    
    def _extractMessageContent(self, firstChoice: dict[str, Any]) -> tuple[str, str]:
        message = firstChoice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            reasoningContent = message.get("reasoning_content")

            return (
                content if isinstance(content, str) else "",
                reasoningContent if isinstance(reasoningContent, str) else "",
            )

        text = firstChoice.get("text")
        if isinstance(text, str):
            return text, ""
        
        raise LlamaCppServerProviderError("llama.cpp response is missing assistant message or text field")
    
    def _extractUsage(self, responseJson: dict[str, Any]) -> ModelUsage:
        usage = responseJson.get("usage")
        if not isinstance(usage, dict):
            return ModelUsage()

        promptTokensDetails = usage.get("prompt_tokens_details")
        cachedPromptTokens = None
        if isinstance(promptTokensDetails, dict):
            cachedPromptTokens = self._extractOptionalInt(promptTokensDetails, "cached_tokens")

        return ModelUsage(
            promptTokens=self._extractOptionalInt(usage, "prompt_tokens"),
            completionTokens=self._extractOptionalInt(usage, "completion_tokens"),
            totalTokens=self._extractOptionalInt(usage, "total_tokens"),
            cachedPromptTokens=cachedPromptTokens,
        )

    def _extractTimings(self, responseJson: dict[str, Any], wallMilliseconds: float) -> ModelTimings:
        timings = responseJson.get("timings")
        if not isinstance(timings, dict):
            return ModelTimings(wallMilliseconds=wallMilliseconds)

        return ModelTimings(
            wallMilliseconds=wallMilliseconds,
            promptMilliseconds=self._extractOptionalFloat(timings, "prompt_ms"),
            predictedMilliseconds=self._extractOptionalFloat(timings, "predicted_ms"),
            predictedTokensPerSecond=self._extractOptionalFloat(timings, "predicted_per_second"),
        )
    
    def _extractOptionalString(self, source: dict[str, Any], key: str) -> str | None:
        value = source.get(key)
        if isinstance(value, str):
            return value
        return None
    
    def _extractOptionalInt(self, source: dict[str, Any], key: str) -> int | None:
        value = source.get(key)
        if isinstance(value, bool):
            return None

        if isinstance(value, int):
            return value

        return None
    
    def _extractOptionalFloat(self, source: dict[str, Any], key: str) -> float | None:
        value = source.get(key)
        if isinstance(value, bool):
            return None

        if isinstance(value, int | float):
            return float(value)

        return None
    
    def _previewAroundPosition(self, text: str, position: int, radius: int = 200) -> str:
        start = max(0, position - radius)
        end = min(len(text), position + radius + 1)

        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""

        return prefix + text[start:end] + suffix
    
    def _previewFromStart(self, text: str, limit: int = 400) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "..."
    
    def _normalizeMessages(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalizedMessages: list[dict[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                raise LlamaCppServerProviderError("Chat message must be an object")
            
            role = str(message.get("role", "user")).lower()
            if role not in {"system", "user", "assistant"}:
                raise LlamaCppServerProviderError(f"Unsupported chat message role: {role}")
            content = "" if message.get("content") is None else str(message.get("content"))
            normalizedMessages.append({"role": role, "content": content})
        return normalizedMessages
