# backend/pipeline/llamaCppServerProvider.py
from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Never
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
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

    @staticmethod
    def forEmptyMessages() -> LlamaCppServerProviderError:
        return LlamaCppServerProviderError("cannot generate chat response without messages")

    @staticmethod
    def forInvalidJsonResponse(
        *,
        reason: str,
        position: int,
        preview: str,
    ) -> LlamaCppServerProviderError:
        msg = f"llama.cpp response is not valid JSON: {reason} at position {position}\n{preview}"
        return LlamaCppServerProviderError(msg)

    @staticmethod
    def forUnsupportedBaseUrlScheme(
        *,
        baseUrl: str,
        scheme: str,
    ) -> LlamaCppServerProviderError:
        msg = f"llama.cpp base URL uses unsupported scheme {scheme!r}: {baseUrl}"
        return LlamaCppServerProviderError(
            msg,
            errorType="unsupported_url_scheme",
            providerDetails={"baseUrl": baseUrl, "scheme": scheme},
        )

    @staticmethod
    def forNonObjectJsonResponse() -> LlamaCppServerProviderError:
        return LlamaCppServerProviderError("llama.cpp returned JSON that is not an object")

    @staticmethod
    def forInvalidHttpErrorBody(
        *,
        statusCode: int,
        preview: str,
    ) -> LlamaCppServerProviderError:
        msg = f"llama.cpp HTTP error {statusCode}: {preview}"
        return LlamaCppServerProviderError(msg, statusCode=statusCode)

    @staticmethod
    def forHttpErrorWithoutErrorObject(
        *,
        statusCode: int,
        preview: str,
        providerDetails: dict[str, Any],
    ) -> LlamaCppServerProviderError:
        msg = f"llama.cpp HTTP error {statusCode}: {preview}"
        return LlamaCppServerProviderError(msg, statusCode=statusCode, providerDetails=providerDetails)

    @staticmethod
    def forHttpError(
        *,
        statusCode: int,
        errorType: str | None,
        serverMessage: str | None,
        fallbackMessage: str,
        providerDetails: dict[str, Any],
    ) -> LlamaCppServerProviderError:
        message = serverMessage or fallbackMessage
        msg = f"llama.cpp {errorType or 'http_error'} {statusCode}: {message}"
        return LlamaCppServerProviderError(
            msg,
            statusCode=statusCode,
            errorType=errorType,
            serverMessage=serverMessage,
            providerDetails=providerDetails,
        )

    @staticmethod
    def forMissingChoices() -> LlamaCppServerProviderError:
        return LlamaCppServerProviderError("llama.cpp returned JSON without a non-empty choices field")

    @staticmethod
    def forInvalidFirstChoice() -> LlamaCppServerProviderError:
        return LlamaCppServerProviderError("llama.cpp first choice is not an object")

    @staticmethod
    def forMissingAssistantContent() -> LlamaCppServerProviderError:
        return LlamaCppServerProviderError("llama.cpp response is missing assistant message or text field")

    @staticmethod
    def forInvalidChatMessage() -> LlamaCppServerProviderError:
        return LlamaCppServerProviderError("chat message must be an object")

    @staticmethod
    def forUnsupportedChatRole(role: str) -> LlamaCppServerProviderError:
        msg = f"unsupported chat message role: {role}"
        return LlamaCppServerProviderError(msg)


class LlamaCppContextExceededError(LlamaCppServerProviderError):
    """Raised when llama.cpp rejects a request because it exceeds context size."""

    def __init__(
        self,
        *,
        promptTokenCount: int | None = None,
        contextSize: int | None = None,
        statusCode: int | None = None,
        serverMessage: str | None = None,
        providerDetails: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            self._makeMessage(promptTokenCount, contextSize, serverMessage),
            statusCode=statusCode,
            errorType="exceed_context_size_error",
            serverMessage=serverMessage,
            providerDetails=providerDetails,
        )
        self.promptTokenCount = promptTokenCount
        self.contextSize = contextSize

    @staticmethod
    def _makeMessage(
        promptTokenCount: int | None,
        contextSize: int | None,
        serverMessage: str | None,
    ) -> str:
        if promptTokenCount is not None and contextSize is not None:
            return (
                f"llama.cpp context exceeded: request used {promptTokenCount} prompt tokens, "
                f"context size is {contextSize}"
            )

        if serverMessage:
            return f"llama.cpp context exceeded: {serverMessage}"

        return "llama.cpp context exceeded"


class LlamaCppServerTimeoutError(LlamaCppServerProviderError):
    """Raised when llama.cpp server does not respond before the timeout."""

    def __init__(
        self,
        *,
        timeoutSeconds: float,
    ) -> None:
        msg = f"llama.cpp request timed out after {timeoutSeconds} seconds"
        super().__init__(
            msg,
            errorType="timeout",
            providerDetails={"timeoutSeconds": timeoutSeconds},
        )
        self.timeoutSeconds = timeoutSeconds


class LlamaCppServerUnreachableError(LlamaCppServerProviderError):
    """Raised when llama.cpp server cannot be reached."""

    def __init__(
        self,
        *,
        baseUrl: str,
        reason: object,
    ) -> None:
        msg = f"llama.cpp server is unreachable at {baseUrl}: {reason}"
        super().__init__(
            msg,
            errorType="server_unreachable",
            providerDetails={"baseUrl": baseUrl, "reason": str(reason)},
        )
        self.baseUrl = baseUrl
        self.reason = reason


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
            raise LlamaCppServerProviderError.forEmptyMessages()

        payload: dict[str, Any] = {
            "messages": self._normalizeMessages(messages),
            "temperature": self.config.temperature,
            "max_tokens": self.config.maxTokens,
            "stream": False,
        }
        if self.config.model:
            payload["model"] = self.config.model

        postResult = self._postJson("/v1/chat/completions", payload)
        return self._parseModelResponse(postResult.responseJson, postResult.wallMilliseconds)

    @staticmethod
    def _validateBaseUrl(baseUrl: str) -> None:
        scheme = urlsplit(baseUrl).scheme.lower()
        if scheme not in {"http", "https"}:
            raise LlamaCppServerProviderError.forUnsupportedBaseUrlScheme(
                baseUrl=baseUrl,
                scheme=scheme or "<missing>",
            )

    def _postJson(self, path: str, payload: dict[str, Any]) -> JsonPostResult:
        self._validateBaseUrl(self.config.baseUrl)
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
            raise LlamaCppServerTimeoutError(timeoutSeconds=self.config.timeoutSeconds) from err
        except URLError as err:
            raise LlamaCppServerUnreachableError(baseUrl=self.config.baseUrl, reason=err.reason) from err
        finally:
            wallMilliseconds = (perf_counter() - startedAt) * 1000.0

        try:
            parsed = json.loads(responseBody)
        except json.JSONDecodeError as err:
            errorPreview = self._previewAroundPosition(responseBody, err.pos)

            raise LlamaCppServerProviderError.forInvalidJsonResponse(
                reason=err.msg,
                position=err.pos,
                preview=errorPreview,
            ) from err

        if not isinstance(parsed, dict):
            raise LlamaCppServerProviderError.forNonObjectJsonResponse()

        return JsonPostResult(responseJson=parsed, wallMilliseconds=wallMilliseconds)

    def _raiseHttpError(self, err: HTTPError, errorBody: str) -> Never:
        parsedError = self._parseErrorBody(errorBody)
        if parsedError is None:
            errorPreview = self._previewFromStart(errorBody)
            raise LlamaCppServerProviderError.forInvalidHttpErrorBody(
                statusCode=err.code,
                preview=errorPreview,
            ) from err

        errorObject = parsedError.get("error")
        if not isinstance(errorObject, dict):
            errorPreview = self._previewFromStart(errorBody)
            raise LlamaCppServerProviderError.forHttpErrorWithoutErrorObject(
                statusCode=err.code,
                preview=errorPreview,
                providerDetails=parsedError,
            ) from err

        errorType = self._extractOptionalString(errorObject, "type")
        serverMessage = self._extractOptionalString(errorObject, "message")
        # TODO: Make distinction between HTTP error response, and JSON message error response, as they can share code
        serverCode = self._extractOptionalInt(errorObject, "code")
        statusCode = serverCode if serverCode is not None else err.code

        if errorType == "exceed_context_size_error":
            promptTokenCount = self._extractOptionalInt(errorObject, "n_prompt_tokens")
            contextSize = self._extractOptionalInt(errorObject, "n_ctx")
            raise LlamaCppContextExceededError(
                promptTokenCount=promptTokenCount,
                contextSize=contextSize,
                statusCode=statusCode,
                serverMessage=serverMessage,
                providerDetails=errorObject,
            ) from err

        fallbackMessage = self._previewFromStart(errorBody)
        raise LlamaCppServerProviderError.forHttpError(
            statusCode=statusCode,
            errorType=errorType,
            serverMessage=serverMessage,
            fallbackMessage=fallbackMessage,
            providerDetails=errorObject,
        ) from err

    @staticmethod
    def _parseErrorBody(errorBody: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(errorBody)
        except json.JSONDecodeError:
            return None

        if isinstance(parsed, dict):
            return parsed

        return None

    def _parseModelResponse(self, responseJson: dict[str, Any], wallMilliseconds: float) -> ModelResponse:
        choices = responseJson.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlamaCppServerProviderError.forMissingChoices()

        firstChoice = choices[0]
        if not isinstance(firstChoice, dict):
            raise LlamaCppServerProviderError.forInvalidFirstChoice()

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

    @staticmethod
    def _extractMessageContent(firstChoice: dict[str, Any]) -> tuple[str, str]:
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

        raise LlamaCppServerProviderError.forMissingAssistantContent()

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

    @staticmethod
    def _extractOptionalString(source: dict[str, Any], key: str) -> str | None:
        value = source.get(key)
        if isinstance(value, str):
            return value
        return None

    @staticmethod
    def _extractOptionalInt(source: dict[str, Any], key: str) -> int | None:
        value = source.get(key)
        if isinstance(value, bool):
            return None

        if isinstance(value, int):
            return value

        return None

    @staticmethod
    def _extractOptionalFloat(source: dict[str, Any], key: str) -> float | None:
        value = source.get(key)
        if isinstance(value, bool):
            return None

        if isinstance(value, int | float):
            return float(value)

        return None

    @staticmethod
    def _previewAroundPosition(text: str, position: int, radius: int = 200) -> str:
        start = max(0, position - radius)
        end = min(len(text), position + radius + 1)

        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""

        return prefix + text[start:end] + suffix

    @staticmethod
    def _previewFromStart(text: str, limit: int = 400) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    @staticmethod
    def _normalizeMessages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalizedMessages: list[dict[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                raise LlamaCppServerProviderError.forInvalidChatMessage()

            role = str(message.get("role", "user")).lower()
            if role not in {"system", "user", "assistant"}:
                raise LlamaCppServerProviderError.forUnsupportedChatRole(role)
            content = "" if message.get("content") is None else str(message.get("content"))
            normalizedMessages.append({"role": role, "content": content})
        return normalizedMessages
