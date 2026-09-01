# file: first-party/llmDrivers/llamaCpp/codeEntry.py ; version: 1
from __future__ import annotations

import json
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
        self.baseUrl = baseUrl.rstrip("/")
        self.contextWindowTokens = contextWindowTokens

    def getExecutionProfile(self, *, model: str | None, providerOptions: Mapping[str, ImmutableValue]) -> LlmExecutionProfile:
        del model, providerOptions
        return LlmExecutionProfile(
            contextWindowTokens=self.contextWindowTokens,
            tokenEstimator=None,
            metadata={"baseUrl": self.baseUrl},
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


def _parseInferenceOptions(source: Mapping[str, ImmutableValue]) -> LlamaCppOptions:
    allowed = {"temperature", "maxTokens", "topP", "topK", "minP", "repeatPenalty", "seed", "timeoutSeconds"}
    unknown = set(source) - allowed
    if unknown:
        raise ValueError(f"Unsupported llama.cpp provider option: {min(unknown)!r}.")
    return LlamaCppOptions(
        temperature=float(source.get("temperature", 0.7)),
        maxTokens=None if source.get("maxTokens") is None else int(source["maxTokens"]),
        topP=None if source.get("topP") is None else float(source["topP"]),
        topK=None if source.get("topK") is None else int(source["topK"]),
        minP=None if source.get("minP") is None else float(source["minP"]),
        repeatPenalty=None if source.get("repeatPenalty") is None else float(source["repeatPenalty"]),
        seed=None if source.get("seed") is None else int(source["seed"]),
        timeoutSeconds=float(source.get("timeoutSeconds", 120.0)),
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
        line = rawLine.decode("utf-8", errors="strict").rstrip("\r\n")
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
        self.baseUrl = str(self.config.get("baseUrl", f"http://{host}:{port}"))
        self.contextWindowTokens = None if self.config.get("contextWindowTokens") is None else int(self.config["contextWindowTokens"])
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if not bool(self.config.get("manageServer", False)):
            return
        if self.process is not None:
            raise RuntimeError("llama-server is already managed by this CodeEntry instance.")
        executable = Path(str(self.config["executable"])).resolve()
        model = Path(str(self.config["modelPath"])).resolve()
        args = [str(executable), "-m", str(model), "--host", str(self.config.get("host", "127.0.0.1")),
                "--port", str(int(self.config.get("port", 8080)))]
        if self.contextWindowTokens is not None:
            args += ["-c", str(self.contextWindowTokens)]
        if self.config.get("gpuLayers") is not None:
            args += ["-ngl", str(int(self.config["gpuLayers"]))]
        if self.config.get("threads") is not None:
            args += ["-t", str(int(self.config["threads"]))]
        if self.config.get("mmprojPath"):
            args += ["--mmproj", str(Path(str(self.config["mmprojPath"])).resolve())]
        extra = self.config.get("extraArgs", [])
        if not isinstance(extra, list) or not all(type(item) is str for item in extra):
            raise ValueError("llamaCpp.extraArgs must be a list of strings.")
        args.extend(extra)
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL)
        self._waitReady(float(self.config.get("startupTimeoutSeconds", 120.0)))

    def _waitReady(self, timeoutSeconds: float) -> None:
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
        self.stop()
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
    driver.start()
    ctx.llm.registerProvider(
        "llama.cpp",
        LlamaCppStreamProvider(baseUrl=driver.baseUrl, contextWindowTokens=driver.contextWindowTokens),
    )
    return driver


def onUnload(ctx, driver):
    del ctx
    if isinstance(driver, LlamaCppDriver):
        driver.stop()
