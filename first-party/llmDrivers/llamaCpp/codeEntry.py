# file: first-party/llmDrivers/llamaCpp/codeEntry.py ; version: 4
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


@dataclass(frozen=True, slots=True)
class LlamaCppModel:
    name: str
    modelPath: Path
    mmprojPath: Path | None
    contextWindowTokens: int | None
    gpuLayers: int | None
    threads: int | None
    extraArgs: tuple[str, ...]


class LlamaCppStreamProvider:
    """Provider-neutral streaming adapter backed by one long-lived llama.cpp driver."""

    def __init__(self, *, driver: "LlamaCppDriver") -> None:
        self.driver = driver

    def getExecutionProfile(self, *, model: str | None, providerOptions: Mapping[str, ImmutableValue]) -> LlmExecutionProfile:
        options = _parseInferenceOptions(providerOptions)
        selected = self.driver.ensureModel(model)
        metadata: dict[str, ImmutableValue] = {
            "baseUrl": self.driver.baseUrl,
            "timeoutSeconds": options.timeoutSeconds,
            "managedServer": self.driver.manageServer,
        }
        if selected is not None:
            metadata.update(
                {
                    "activeModel": selected.name,
                    "modelPath": str(selected.modelPath),
                },
            )
            if selected.mmprojPath is not None:
                metadata["mmprojPath"] = str(selected.mmprojPath)
        return LlmExecutionProfile(
            contextWindowTokens=(
                selected.contextWindowTokens
                if selected is not None
                else self.driver.externalContextWindowTokens
            ),
            tokenEstimator=None,
            metadata=metadata,
        )

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        selected = self.driver.ensureModel(request.model)
        options = _parseInferenceOptions(request.providerOptions)
        payload = _buildPayload(
            request,
            options,
            includeRequestedModel=not self.driver.manageServer,
        )
        endpoint = f"{self.driver.baseUrl}/v1/chat/completions"
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
            active = "" if selected is None else f" for model {selected.name!r}"
            raise LlmProviderConnectionError(
                f"llama.cpp returned HTTP {err.code} for {endpoint}{active}.",
            ) from err
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


def _buildPayload(request: LlmCallRequest, options: LlamaCppOptions, *, includeRequestedModel: bool) -> dict[str, object]:
    if request.query.formatId != "text/plain":
        raise ValueError(f"llama.cpp proving-ground provider supports only text/plain, not {request.query.formatId!r}.")
    if type(request.query.payload) is not str:
        raise TypeError("text/plain LlmQuery payload must be an exact built-in string.")
    payload: dict[str, object] = {
        "messages": [{"role": "user", "content": request.query.payload}],
        "stream": True,
        "temperature": options.temperature,
    }
    if includeRequestedModel and request.model is not None:
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
        if "error" in chunk:
            raise LlmProviderProtocolError(f"llama.cpp stream reported an error: {chunk['error']!r}.")
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


def _positiveInt(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive exact integer.")
    return value


def _optionalPositiveInt(value: object, name: str) -> int | None:
    return None if value is None else _positiveInt(value, name)


def _optionalNonNegativeInt(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative exact integer.")
    return value


def _stringList(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(type(item) is str for item in value):
        raise ValueError(f"{name} must be a list of strings.")
    return tuple(value)


class LlamaCppDriver:
    """Long-lived CodeEntry-owned llama-server and model-residency manager."""

    def __init__(self, config: Mapping[str, object]) -> None:
        self.config = dict(config)
        manageValue = self.config.get("manageServer", False)
        if type(manageValue) is not bool:
            raise TypeError("llamaCpp.manageServer must be a boolean.")
        self.manageServer = manageValue

        hostValue = self.config.get("host", "127.0.0.1")
        if type(hostValue) is not str or not hostValue:
            raise ValueError("llamaCpp.host must be a non-empty string.")
        self.host = hostValue
        self.port = _positiveInt(self.config.get("port", 8080), "llamaCpp.port")
        if self.port > 65535:
            raise ValueError("llamaCpp.port must not exceed 65535.")
        baseUrl = self.config.get("baseUrl", f"http://{self.host}:{self.port}")
        if type(baseUrl) is not str or not baseUrl.strip():
            raise ValueError("llamaCpp.baseUrl must be a non-blank string.")
        self.baseUrl = baseUrl.rstrip("/")
        self.externalContextWindowTokens = _optionalPositiveInt(
            self.config.get("contextWindowTokens"),
            "llamaCpp.contextWindowTokens",
        )

        self.executable: Path | None = None
        if self.manageServer:
            executableValue = self.config.get("executable")
            if type(executableValue) is not str or not executableValue:
                raise ValueError("Managed llama.cpp requires llamaCpp.executable.")
            self.executable = Path(executableValue).resolve()
            if not self.executable.is_file():
                raise FileNotFoundError(f"llama-server executable does not exist: {self.executable}.")

        self.models = self._buildModels()
        defaultValue = self.config.get("defaultModel")
        if self.models:
            if defaultValue is None:
                if len(self.models) != 1:
                    raise ValueError("llamaCpp.defaultModel is required when multiple models are configured.")
                defaultValue = next(iter(self.models))
            if type(defaultValue) is not str or defaultValue not in self.models:
                raise ValueError("llamaCpp.defaultModel must name one configured model.")
            self.defaultModel: str | None = defaultValue
        else:
            self.defaultModel = None

        self.process: subprocess.Popen[bytes] | None = None
        self.activeModelName: str | None = None

    def _buildModels(self) -> dict[str, LlamaCppModel]:
        source = self.config.get("models")
        if source is None:
            if not self.manageServer:
                return {}
            modelPath = self.config.get("modelPath")
            if type(modelPath) is not str or not modelPath:
                raise ValueError("Managed llama.cpp requires modelPath or a models mapping.")
            nameValue = self.config.get("defaultModel", "default")
            if type(nameValue) is not str or not nameValue:
                raise ValueError("llamaCpp.defaultModel must be a non-empty string.")
            source = {nameValue: {"modelPath": modelPath}}
        if not isinstance(source, dict):
            raise ValueError("llamaCpp.models must be an object keyed by model name.")

        models: dict[str, LlamaCppModel] = {}
        for name, raw in source.items():
            if type(name) is not str or not name:
                raise ValueError("llamaCpp.models keys must be non-empty strings.")
            if not isinstance(raw, dict):
                raise ValueError(f"llamaCpp.models[{name!r}] must be an object.")
            modelPathValue = raw.get("modelPath")
            if type(modelPathValue) is not str or not modelPathValue:
                raise ValueError(f"llamaCpp.models[{name!r}].modelPath is required.")
            modelPath = Path(modelPathValue).resolve()
            if self.manageServer and not modelPath.is_file():
                raise FileNotFoundError(f"llama.cpp model does not exist: {modelPath}.")

            mmprojValue = raw.get("mmprojPath", self.config.get("mmprojPath"))
            mmprojPath: Path | None = None
            if mmprojValue is not None:
                if type(mmprojValue) is not str or not mmprojValue:
                    raise ValueError(f"llamaCpp.models[{name!r}].mmprojPath must be a non-empty string.")
                mmprojPath = Path(mmprojValue).resolve()
                if self.manageServer and not mmprojPath.is_file():
                    raise FileNotFoundError(f"llama.cpp mmproj does not exist: {mmprojPath}.")

            contextWindow = _optionalPositiveInt(
                raw.get("contextWindowTokens", self.config.get("contextWindowTokens")),
                f"llamaCpp.models[{name!r}].contextWindowTokens",
            )
            gpuLayers = _optionalNonNegativeInt(
                raw.get("gpuLayers", self.config.get("gpuLayers")),
                f"llamaCpp.models[{name!r}].gpuLayers",
            )
            threads = _optionalPositiveInt(
                raw.get("threads", self.config.get("threads")),
                f"llamaCpp.models[{name!r}].threads",
            )
            extra = raw.get("extraArgs", self.config.get("extraArgs", []))
            models[name] = LlamaCppModel(
                name=name,
                modelPath=modelPath,
                mmprojPath=mmprojPath,
                contextWindowTokens=contextWindow,
                gpuLayers=gpuLayers,
                threads=threads,
                extraArgs=_stringList(extra, f"llamaCpp.models[{name!r}].extraArgs"),
            )
        return models

    def start(self) -> None:
        if self.manageServer:
            self.ensureModel(self.defaultModel)

    def ensureModel(self, model: str | None) -> LlamaCppModel | None:
        if not self.models:
            return None
        selectedName = self.defaultModel if model is None else model
        if selectedName is None:
            raise RuntimeError("llama.cpp has no model selected.")
        try:
            selected = self.models[selectedName]
        except KeyError as err:
            raise LookupError(f"llama.cpp model is not configured: {selectedName!r}.") from err

        if not self.manageServer:
            return selected
        if self.activeModelName == selectedName and self.process is not None and self.process.poll() is None:
            return selected

        self.stop()
        self._startModel(selected)
        return selected

    def _startModel(self, model: LlamaCppModel) -> None:
        if self.executable is None:
            raise RuntimeError("Managed llama.cpp executable is unavailable.")
        args = [
            str(self.executable),
            "-m",
            str(model.modelPath),
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        if model.contextWindowTokens is not None:
            args += ["-c", str(model.contextWindowTokens)]
        if model.gpuLayers is not None:
            args += ["-ngl", str(model.gpuLayers)]
        if model.threads is not None:
            args += ["-t", str(model.threads)]
        if model.mmprojPath is not None:
            args += ["--mmproj", str(model.mmprojPath)]
        args.extend(model.extraArgs)

        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL)
        self.activeModelName = model.name
        try:
            self._waitReady(float(self.config.get("startupTimeoutSeconds", 120.0)))
        except Exception:
            self.stop()
            raise

    def _waitReady(self, timeoutSeconds: float) -> None:
        if not math.isfinite(timeoutSeconds) or timeoutSeconds <= 0:
            raise ValueError("llamaCpp.startupTimeoutSeconds must be a positive finite number.")
        deadline = time.monotonic() + timeoutSeconds
        health = f"{self.baseUrl}/health"
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
        self.activeModelName = None
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
        ctx.llm.registerProvider("llama.cpp", LlamaCppStreamProvider(driver=driver))
    except Exception:
        driver.stop()
        raise
    return driver


def onUnload(ctx, driver):
    del ctx
    if isinstance(driver, LlamaCppDriver):
        driver.stop()
