# llamaCppServerProvider.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LlamaCppServerProviderError(RuntimeError):
    """Raised when llama.cpp server communication or response parsing fails."""


@dataclass(frozen=True)
class LlamaCppServerConfig:
    """Configuration for llama.cpp server chat completion."""
    
    baseUrl: str = "http://localhost:1234"
    model: str = ""
    temperature: float = 0.6
    maxTokens: int = 512
    timeoutSeconds: float = 120.0


class LlamaCppServerProvider:
    """Synchronous llama.cpp chat provider."""
    
    def __init__(self, config: LlamaCppServerConfig | None = None) -> None:
        self.config = config or LlamaCppServerConfig()

    def generateChatResponse(self, messages: list[dict[str, Any]]) -> str:
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
        
        responseJson = self._postJson("/v1/chat/completions", payload)
        content = self._extractAssistantContent(responseJson)
        if not content.strip():
            raise LlamaCppServerProviderError("llama.cpp returned an empty assistant response")
        return content
    
    def _postJson(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
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
        
        try:
            with urlopen(request, timeout=self.config.timeoutSeconds) as response:
                responseBody = response.read().decode("utf-8", errors="replace")
        except HTTPError as err:
            errorBody = err.read().decode("utf-8", errors="replace")
            errorPreview = self._previewFromStart(errorBody)
            
            raise LlamaCppServerProviderError(
                f"llama.cpp HTTP error {err.code}: {errorPreview}"
            ) from err
        except TimeoutError as err:
            raise LlamaCppServerProviderError(
                f"llama.cpp request timed out after {self.config.timeoutSeconds} seconds"
            ) from err
        except URLError as err:
            raise LlamaCppServerProviderError(
                f"llama.cpp server is not reachable at {self.config.baseUrl}: {err.reason}"
            ) from err
        
        try:
            parsed = json.loads(responseBody)
        except json.JSONDecodeError as err:
            errorPreview = self._previewAroundPosition(responseBody, err.pos)
            
            raise LlamaCppServerProviderError(
                f"llama.cpp response is not valid JSON: {err.msg} at position {err.pos}\n{errorPreview}"
            ) from err
        
        if not isinstance(parsed, dict):
            raise LlamaCppServerProviderError("llama.cpp returned JSON that is not an object")
        
        return parsed
    
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
    
    def _extractAssistantContent(self, responseJson: dict[str, Any]) -> str:
        choices = responseJson.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlamaCppServerProviderError("llama.cpp returned JSON without a non-empty choices field")
        
        firstChoice = choices[0]
        if not isinstance(firstChoice, dict):
            raise LlamaCppServerProviderError("llama.cpp first choice is not an object")
        
        message = firstChoice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
        
        text = firstChoice.get("text")
        if isinstance(text, str):
            return text
        
        raise LlamaCppServerProviderError("llama.cpp response is missing assistant content field")
    
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
