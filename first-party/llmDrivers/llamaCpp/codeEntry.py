# file: first-party/llmDrivers/llamaCpp/codeEntry.py ; version: 2
from __future__ import annotations

import json
import math
import subprocess
import time
import urllib.request as urlRequest
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.llm.errors import LlmProviderConnectionError, LlmProviderProtocolError
from backend.llm.llmTypes import LlmCallRequest, LlmExecutionProfile, LlmStreamEvent


@dataclass(frozen=True, slots=True)
class LlamaCppOptions:
    temperature: float = 0.7
    maxTokens: int | None = None
    topP: float | None = None
    topK: int | None = None
    minP: float | None = None
    repeatPenalty: float | None = None
    seed: int | None = None
    timeoutSeconds: float = 120.0


class LlamaCppStreamProvider:
    """Provider-neutral streaming adapter for an OpenAI-compatible llama-server."""

    def __init__(self, *, baseUrl: str, contextWindowTokens: int | None = None) -> None:
        if type(baseUrl) is not str or not baseUrl.strip():
            raise ValueError("llama.cpp baseUrl must be a non-blank string.")
        if contextWindowTokens is not None and (type(contextWindowTokens) is not int or contextWindowTokens <= 0):
            raise ValueError("contextWindowTokens must be a positive exact integer when supplied.")
        self.baseUrl = baseUrl.rstrip("/")
        self.contextWindowTokens = contextWindowTokens

    def getExecutionProfile(self, *, model: str | None, providerOptions: Mapping[str, ImmutableValue]) -> LlmExecutionProfile:
        del model
        options = _parseInferenceOptions(providerOptions)
        return LlmExecutionProfile(
            contextWindowTokens=self.contextWindowTokens,
            tokenEstimator=None,
            metadata={
                "baseUrl": self.baseUrl,
                "timeoutSeconds": options.timeoutSeconds,
            },
        )

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        options = _parseInferenceOptions(request.providerOptions)
        payload = _buildPayload(request, options)
        endpoint = f"{self.baseUrl}/v1/chat/completions"
        httpRequest = urlRequest.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlRequest.urlopen(httpRequest, timeout=options.timeoutSeconds) as response:
                yield from _readEvents(response)
        except HTTPError as err:
            raise LlmProviderConnectionError(f"llama.cpp returned HTTP {err.code} for {endpoint}.") from err
        except (URLError, TimeoutError) as err:
            raise LlmProviderConnectionError(f"Failed communicating with llama.cpp at {endpoint}.") from err


def _optionalFloat(source: Mapping[str, ImmutableValue], key: str, *, minimum: float | None = None,
                   maximum: float | None = None, strictlyPositive: bool = False) -> float | None:
    value = source.get(key)
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise TypeError(f"llama.cpp provider option {key!r} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"llama.cpp provider option {key!r} must be finite.")
    if strictlyPositive and result <= 0:
        raise ValueError(f"llama.cpp provider option {key!r} must be positive.")
    if minimum is not None and result < minimum:
        raise ValueError(f"llama.cpp provider option {key!r} must be >= {minimum}.")
    if maximum is not None and result > maximum:
        raise ValueError(f"llama.cpp provider option {key!r} must be <= {maximum}.")
    return result


def _optionalInt(source: Mapping[str, ImmutableValue], key: str, *, positive: bool = False) -> int | None:
    value = source.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"llama.cpp provider option {key!r} must be an exact integer.")
    if positive and value <= 0:
        raise ValueError(f"llama.cpp provider option {key!r} must be positive.")
    return value


def _parseInferenceOptions(source: Mapping[str, ImmutableValue]) -> LlamaCppOptions:
    allowed = {"temperature", "maxTokens", "topP", "topK", "minP", "repeatPenalty", "seed", "timeoutSeconds"}
    unknown = set(source) - allowed
    if unknown:
        raise ValueError(f"Unsupported llama.cpp provider option: {min(unknown)!r}.")

    temperature = _optionalFloat(source, "temperature", minimum=0.0)
    timeout = _optionalFloat(source, "timeoutSeconds", strictlyPositive=True)
    return LlamaCppOptions(
        temperature=0.7 if temperature is None else temperature,
        maxTokens=_optionalInt(source, "maxTokens", positive=True),
        topP=_optionalFloat(source, "topP", minimum=0.0, maximum=1.0),
        topK=_optionalInt(source, "topK", positive=True),
        minP=_optionalFloat(source, "minP", minimum=0.0, maximum=1.0),
        repeatPenalty=_optionalFloat(source, "repeatPenalty", strictlyPositive=True),
        seed=_optionalInt(source, "seed"),
        timeoutSeconds=120.0 if timeout is None else timeout,
    )


def _buildPayload(request: LlmCallRequest, options: LlamaCppOptions) -> dict[str, object]:
    if request.query.formatId != "text/plain":
        raise ValueError(f"llama.cpp proving-ground provider supports only text/plain, not {request.query.formatId!r}.")
    if type(request.query.payload) is not str:
        raise TypeError("text/plain LlmQuery payload must be an exact built-in string.")
    payload: dict[str, object] = {
        "messages": [{"role": "user", "content": request.query.payload}],
        "stream": True,
        "temperature": options.temperature,
    }
    if request.model is not None:
        payload["model"] = request.model
    if options.maxTokens is not None:
        payload["max_tokens"] = options.maxTokens
    if options.topP is not None:
        payload["top_p"] = options.topP
    if options.topK is not None:
        payload["top_k"] = options.topK
    if options.minP is not None:
        payload["min_p"] = options.minP
    if options.repeatPenalty is not None:
        payload["repeat_penalty"] = options.repeatPenalty
    if options.seed is not None:
        payload["seed"] = options.seed
    return payload


def _readEvents(response) -> Iterator[LlmStreamEvent]:
    finalMetadata: dict[str, ImmutableValue] = {}
    for rawLine in response:
        if not isinstance(rawLine, bytes):
            raise LlmProviderProtocolError("llama.cpp stream produced a non-bytes line.")
        try:
            line = rawLine.decode("utf-8", errors="strict").rstrip("\r\n")
        except UnicodeDecodeError as err:
            raise LlmProviderProtocolError("llama.cpp stream contained invalid UTF-8.") from err
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            yield LlmStreamEvent(eventType="completed", metadata=finalMetadata)
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as err:
            raise LlmProviderProtocolError("llama.cpp stream contained invalid JSON.") from err
        if not isinstance(chunk, dict):
            raise LlmProviderProtocolError("llama.cpp stream chunk must be a JSON object.")
        metadataSource = {key: chunk[key] for key in ("model", "usage", "timings", "system_fingerprint") if key in chunk}
        metadata = ImmutableValueFreezer().freezeMapping(metadataSource, "llamaCppStreamMetadata")
        finalMetadata.update(metadata)
        choices = chunk.get("choices", [])
        if not isinstance(choices, list):
            raise LlmProviderProtocolError("llama.cpp stream choices must be a list.")
        text = ""
        if choices:
            choice = choices[0]
            if not isinstance(choice, dict):
                raise LlmProviderProtocolError("llama.cpp stream choice must be an object.")
            delta = choice.get("delta")
            if delta is not None:
                if not isinstance(delta, dict):
                    raise LlmProviderProtocolError("llama.cpp stream delta must be an object.")
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise LlmProviderProtocolError("llama.cpp stream content must be text.")
                    text = content
        if text or metadata:
            yield LlmStreamEvent(eventType="delta", text=text, metadata=metadata)


class LlamaCppDriver:
    """Long-lived CodeEntry-owned llama-server process resource."""

    def __init__(self, config: Mapping[str, object]) -> None:
        self.config = dict(config)
        host = str(self.config.get("host", "127.0.0.1"))
        port = int(self.config.get("port", 8080))
        if not 1 <= port <= 65535:
            raise ValueError("llamaCpp.port must be between 1 and 65535.")
        self.baseUrl = str(self.config.get("baseUrl", f"http://{host}:{port}"))
        contextWindow = self.config.get("contextWindowTokens")
        if contextWindow is not None and (type(contextWindow) is not int or contextWindow <= 0):
            raise ValueError("llamaCpp.contextWindowTokens must be a positive exact integer.")
        self.contextWindowTokens = contextWindow
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if not bool(self.config.get("manageServer", False)):
            return
        if self.process is not None:
            raise RuntimeError("llama-server is already managed by this CodeEntry instance.")
        executable = Path(str(self.config["executable"])).resolve()
        model = Path(str(self.config["modelPath"])).resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"llama-server executable does not exist: {executable}.")
        if not model.is_file():
            raise FileNotFoundError(f"llama.cpp model does not exist: {model}.")
        args = [str(executable), "-m", str(model), "--host", str(self.config.get("host", "127.0.0.1")),
                "--port", str(int(self.config.get("port", 8080)))]
        if self.contextWindowTokens is not None:
            args += ["-c", str(self.contextWindowTokens)]
        if self.config.get("gpuLayers") is not None:
            args += ["-ngl", str(int(self.config["gpuLayers"]))]
        if self.config.get("threads") is not None:
            args += ["-t", str(int(self.config["threads"]))]
        if self.config.get("mmprojPath"):
            mmproj = Path(str(self.config["mmprojPath"])).resolve()
            if not mmproj.is_file():
                raise FileNotFoundError(f"llama.cpp mmproj does not exist: {mmproj}.")
            args += ["--mmproj", str(mmproj)]
        extra = self.config.get("extraArgs", [])
        if not isinstance(extra, list) or not all(type(item) is str for item in extra):
            raise ValueError("llamaCpp.extraArgs must be a list of strings.")
        args.extend(extra)
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL)
        try:
            self._waitReady(float(self.config.get("startupTimeoutSeconds", 120.0)))
        except Exception:
            self.stop()
            raise

    def _waitReady(self, timeoutSeconds: float) -> None:
        if not math.isfinite(timeoutSeconds) or timeoutSeconds <= 0:
            raise ValueError("llamaCpp.startupTimeoutSeconds must be a positive finite number.")
        deadline = time.monotonic() + timeoutSeconds
        health = f"{self.baseUrl.rstrip('/')}/health"
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(f"llama-server exited during startup with code {self.process.returncode}.")
            try:
                with urlRequest.urlopen(health, timeout=1.0) as response:
                    if 200 <= response.status < 300:
                        return
            except (URLError, HTTPError, TimeoutError):
                pass
            time.sleep(0.2)
        raise TimeoutError(f"llama-server did not become ready within {timeoutSeconds} seconds.")

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def onLoad(ctx):
    config = ctx.config.get("llamaCpp", {})
    if not isinstance(config, dict):
        raise ValueError("llamaCpp configuration must be an object.")
    driver = LlamaCppDriver(config)
    try:
        driver.start()
        ctx.llm.registerProvider(
            "llama.cpp",
            LlamaCppStreamProvider(baseUrl=driver.baseUrl, contextWindowTokens=driver.contextWindowTokens),
        )
    except Exception:
        driver.stop()
        raise
    return driver


def onUnload(ctx, driver):
    del ctx
    if isinstance(driver, LlamaCppDriver):
        driver.stop()
