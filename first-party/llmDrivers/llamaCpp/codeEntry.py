# file: first-party/llmDrivers/llamaCpp/codeEntry.py ; version: 7
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
from backend.llm.llmTypes import (
    LlmCallRequest,
    LlmExecutionProfile,
    LlmQuery,
    LlmStreamEvent,
)

_LOAD_MODES = frozenset({"auto", "none", "mmap", "mlock", "mmap+mlock", "dio"})
_FIRST_CLASS_FLAGS = frozenset(
    {
        "--parallel", "-np", "--ctx-size", "-c", "--n-gpu-layers", "-ngl",
        "--threads", "-t", "--threads-batch", "-tb", "--batch-size", "-b",
        "--ubatch-size", "-ub", "--kv-unified", "--no-kv-unified",
        "--kv-offload", "--no-kv-offload", "--cache-type-k", "--cache-type-v",
        "--flash-attn", "--n-cpu-moe", "--load-mode", "--mmproj",
    }
)


@dataclass(frozen=True, slots=True)
class LlamaCppOptions:
    """Validated request-level options for one llama.cpp inference."""

    temperature: float = 0.7
    maxTokens: int | None = None
    topP: float | None = None
    topK: int | None = None
    minP: float | None = None
    repeatPenalty: float | None = None
    seed: int | None = None
    timeoutSeconds: float = 120.0
    reasoningEffort: str | None = None


@dataclass(frozen=True, slots=True)
class LlamaCppModel:
    """Effective validated launch configuration for one resident model."""

    name: str
    modelPath: Path
    mmprojPath: Path | None
    contextWindowTokens: int | None
    gpuLayers: int | None
    threads: int | None
    threadsBatch: int | None
    batchSize: int | None
    ubatchSize: int | None
    unifiedKv: bool | None
    kvOffload: bool | None
    cacheTypeK: str | None
    cacheTypeV: str | None
    flashAttention: bool | None
    cpuMoeLayers: int | None
    loadMode: str | None
    parallelSlots: int
    extraArgs: tuple[str, ...]

    def launchProfile(self) -> dict[str, ImmutableValue]:
        """Returns stable JSON-compatible evidence of effective launch settings."""
        profile: dict[str, ImmutableValue] = {
            "modelPath": str(self.modelPath),
            "parallelSlots": self.parallelSlots,
        }
        optional = {
            "mmprojPath": None if self.mmprojPath is None else str(self.mmprojPath),
            "contextWindowTokens": self.contextWindowTokens,
            "gpuLayers": self.gpuLayers,
            "threads": self.threads,
            "threadsBatch": self.threadsBatch,
            "batchSize": self.batchSize,
            "ubatchSize": self.ubatchSize,
            "unifiedKv": self.unifiedKv,
            "kvOffload": self.kvOffload,
            "cacheTypeK": self.cacheTypeK,
            "cacheTypeV": self.cacheTypeV,
            "flashAttention": self.flashAttention,
            "cpuMoeLayers": self.cpuMoeLayers,
            "loadMode": self.loadMode,
        }
        profile.update({key: value for key, value in optional.items() if value is not None})
        if self.extraArgs:
            profile["extraArgs"] = self.extraArgs
        return profile


class LlamaCppTokenEstimator:
    """Exact text/plain query estimator backed by the active llama.cpp model."""

    def __init__(self, *, driver: "LlamaCppDriver", timeoutSeconds: float) -> None:
        """Binds estimation to one driver and request timeout."""
        self._driver = driver
        self._timeoutSeconds = timeoutSeconds

    def estimateInputTokens(self, query: LlmQuery) -> int:
        """Returns the exact token count after llama.cpp chat-template expansion."""
        if query.formatId != "text/plain" or type(query.payload) is not str:
            raise ValueError(
                "llama.cpp token estimation supports exact-string text/plain queries only."
            )
        templated = self._driver.postJson(
            "/apply-template",
            {"messages": [{"role": "user", "content": query.payload}]},
            timeoutSeconds=self._timeoutSeconds,
        )
        prompt = templated.get("prompt")
        if type(prompt) is not str:
            raise LlmProviderProtocolError(
                "llama.cpp /apply-template response does not contain a string prompt."
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
            raise LlmProviderProtocolError(
                "llama.cpp /tokenize response does not contain a tokens list."
            )
        return len(tokens)


class LlamaCppStreamProvider:
    """Provider-neutral streaming adapter backed by one long-lived llama.cpp driver."""

    def __init__(self, *, driver: "LlamaCppDriver") -> None:
        """Binds the provider to the CodeEntry-owned driver."""
        self.driver = driver

    def getExecutionProfile(
        self,
        *,
        model: str | None,
        providerOptions: Mapping[str, ImmutableValue],
    ) -> LlmExecutionProfile:
        """Resolves model residency and returns benchmark-relevant execution evidence."""
        options = _parseInferenceOptions(providerOptions)
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
        """Streams one OpenAI-compatible chat-completions request from llama.cpp."""
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
                f"llama.cpp returned HTTP {err.code} for {endpoint}{active}."
            ) from err
        except (URLError, TimeoutError) as err:
            raise LlmProviderConnectionError(
                f"Failed communicating with llama.cpp at {endpoint}."
            ) from err


def _optionalFloat(
    source: Mapping[str, ImmutableValue],
    key: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strictlyPositive: bool = False,
) -> float | None:
    """Reads and validates one optional finite numeric provider option."""
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


def _optionalInt(
    source: Mapping[str, ImmutableValue],
    key: str,
    *,
    positive: bool = False,
) -> int | None:
    """Reads and validates one optional exact-integer provider option."""
    value = source.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"llama.cpp provider option {key!r} must be an exact integer.")
    if positive and value <= 0:
        raise ValueError(f"llama.cpp provider option {key!r} must be positive.")
    return value


def _parseInferenceOptions(source: Mapping[str, ImmutableValue]) -> LlamaCppOptions:
    """Parses the complete supported request-level llama.cpp option set."""
    allowed = {
        "temperature", "maxTokens", "topP", "topK", "minP", "repeatPenalty",
        "seed", "timeoutSeconds", "reasoningEffort",
    }
    unknown = set(source) - allowed
    if unknown:
        raise ValueError(f"Unsupported llama.cpp provider option: {min(unknown)!r}.")
    temperature = _optionalFloat(source, "temperature", minimum=0.0)
    timeout = _optionalFloat(source, "timeoutSeconds", strictlyPositive=True)
    reasoning = source.get("reasoningEffort")
    if reasoning is not None and (type(reasoning) is not str or not reasoning.strip()):
        raise ValueError("llama.cpp provider option 'reasoningEffort' must be non-empty text.")
    return LlamaCppOptions(
        temperature=0.7 if temperature is None else temperature,
        maxTokens=_optionalInt(source, "maxTokens", positive=True),
        topP=_optionalFloat(source, "topP", minimum=0.0, maximum=1.0),
        topK=_optionalInt(source, "topK", positive=True),
        minP=_optionalFloat(source, "minP", minimum=0.0, maximum=1.0),
        repeatPenalty=_optionalFloat(source, "repeatPenalty", strictlyPositive=True),
        seed=_optionalInt(source, "seed"),
        timeoutSeconds=120.0 if timeout is None else timeout,
        reasoningEffort=reasoning,
    )


def _buildPayload(
    request: LlmCallRequest,
    options: LlamaCppOptions,
    *,
    includeRequestedModel: bool,
) -> dict[str, object]:
    """Builds one exact OpenAI-compatible request payload from Actant options."""
    if request.query.formatId != "text/plain":
        raise ValueError(
            "llama.cpp proving-ground provider supports only text/plain, not "
            f"{request.query.formatId!r}."
        )
    if type(request.query.payload) is not str:
        raise TypeError("text/plain LlmQuery payload must be an exact built-in string.")
    payload: dict[str, object] = {
        "messages": [{"role": "user", "content": request.query.payload}],
        "stream": True,
        "temperature": options.temperature,
    }
    if includeRequestedModel and request.model is not None:
        payload["model"] = request.model
    mappings = (
        ("max_tokens", options.maxTokens), ("top_p", options.topP),
        ("top_k", options.topK), ("min_p", options.minP),
        ("repeat_penalty", options.repeatPenalty), ("seed", options.seed),
        ("reasoning_effort", options.reasoningEffort),
    )
    for key, value in mappings:
        if value is not None:
            payload[key] = value
    return payload


def _readEvents(response) -> Iterator[LlmStreamEvent]:
    """Parses llama.cpp SSE bytes into provider-neutral stream events."""
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
            raise LlmProviderProtocolError(
                f"llama.cpp stream reported an error: {chunk['error']!r}."
            )
        metadataSource = {
            key: chunk[key]
            for key in ("model", "usage", "timings", "system_fingerprint")
            if key in chunk
        }
        metadata = ImmutableValueFreezer().freezeMapping(
            metadataSource,
            "llamaCppStreamMetadata",
        )
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
    """Validates and returns one positive exact integer."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive exact integer.")
    return value


def _optionalPositiveInt(value: object, name: str) -> int | None:
    """Validates an optional positive exact integer."""
    return None if value is None else _positiveInt(value, name)


def _optionalNonNegativeInt(value: object, name: str) -> int | None:
    """Validates an optional non-negative exact integer."""
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative exact integer.")
    return value


def _optionalBool(value: object, name: str) -> bool | None:
    """Validates an optional exact boolean."""
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean.")
    return value


def _optionalString(value: object, name: str) -> str | None:
    """Validates an optional non-empty exact string while preserving its value."""
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    return value


def _optionalLoadMode(value: object, name: str) -> str | None:
    """Validates one optional llama.cpp load-mode identifier."""
    result = _optionalString(value, name)
    if result is not None and result not in _LOAD_MODES:
        allowed = ", ".join(sorted(_LOAD_MODES))
        raise ValueError(f"{name} must be one of: {allowed}.")
    return result


def _stringList(value: object, name: str) -> tuple[str, ...]:
    """Validates a JSON-style list of exact strings."""
    if not isinstance(value, list) or not all(type(item) is str for item in value):
        raise ValueError(f"{name} must be a list of strings.")
    return tuple(value)


def _rejectFirstClassArgs(extraArgs: tuple[str, ...], name: str) -> None:
    """Rejects escape-hatch flags whose authority belongs to structured fields."""
    for argument in extraArgs:
        flag = argument.split("=", 1)[0]
        if flag in _FIRST_CLASS_FLAGS:
            raise ValueError(
                f"{name} contains first-class flag {flag!r}; use its structured setting."
            )


class LlamaCppDriver:
    """Long-lived CodeEntry-owned llama-server and model-residency manager."""

    def __init__(self, config: Mapping[str, object]) -> None:
        """Validates driver configuration without starting a managed server."""
        self.config = dict(config)
        manageValue = self.config.get("manageServer", False)
        if type(manageValue) is not bool:
            raise TypeError("llamaCpp.manageServer must be a boolean.")
        self.manageServer = manageValue
        hostValue = self.config.get("host", "127.0.0.1")
        if type(hostValue) is not str or not hostValue.strip():
            raise ValueError("llamaCpp.host must be a non-empty string.")
        self.host = hostValue.strip()
        self.port = _positiveInt(self.config.get("port", 8080), "llamaCpp.port")
        if self.port > 65535:
            raise ValueError("llamaCpp.port must not exceed 65535.")
        managedBaseUrl = f"http://{self.host}:{self.port}"
        baseUrl = self.config.get("baseUrl", managedBaseUrl)
        if type(baseUrl) is not str or not baseUrl.strip():
            raise ValueError("llamaCpp.baseUrl must be a non-blank string.")
        self.baseUrl = baseUrl.strip().rstrip("/")
        if self.manageServer and self.baseUrl != managedBaseUrl:
            raise ValueError(
                "Managed llama.cpp baseUrl must identify the server Actant launches at "
                f"{managedBaseUrl!r}; received {self.baseUrl!r}."
            )
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
                raise FileNotFoundError(
                    f"llama-server executable does not exist: {self.executable}."
                )
        self.models = self._buildModels()
        defaultValue = self.config.get("defaultModel")
        if self.models:
            if defaultValue is None:
                if len(self.models) != 1:
                    raise ValueError(
                        "llamaCpp.defaultModel is required when multiple models are configured."
                    )
                defaultValue = next(iter(self.models))
            if type(defaultValue) is not str or defaultValue not in self.models:
                raise ValueError("llamaCpp.defaultModel must name one configured model.")
            self.defaultModel: str | None = defaultValue
        else:
            self.defaultModel = None
        self.process: subprocess.Popen[bytes] | None = None
        self.activeModelName: str | None = None

    def _modelValue(self, raw: Mapping[str, object], key: str) -> object:
        """Resolves one model field over its top-level driver default."""
        return raw[key] if key in raw else self.config.get(key)

    def _buildModels(self) -> dict[str, LlamaCppModel]:
        """Builds immutable effective model configurations with override precedence."""
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
            prefix = f"llamaCpp.models[{name!r}]"
            modelPathValue = raw.get("modelPath")
            if type(modelPathValue) is not str or not modelPathValue:
                raise ValueError(f"{prefix}.modelPath is required.")
            modelPath = Path(modelPathValue).resolve()
            if self.manageServer and not modelPath.is_file():
                raise FileNotFoundError(f"llama.cpp model does not exist: {modelPath}.")
            mmprojValue = self._modelValue(raw, "mmprojPath")
            mmprojPath: Path | None = None
            if mmprojValue is not None:
                if type(mmprojValue) is not str or not mmprojValue:
                    raise ValueError(f"{prefix}.mmprojPath must be a non-empty string.")
                mmprojPath = Path(mmprojValue).resolve()
                if self.manageServer and not mmprojPath.is_file():
                    raise FileNotFoundError(f"llama.cpp mmproj does not exist: {mmprojPath}.")
            parallelValue = (
                raw["parallelSlots"] if "parallelSlots" in raw
                else self.config.get("parallelSlots", 1)
            )
            extraValue = (
                raw["extraArgs"] if "extraArgs" in raw
                else self.config.get("extraArgs", [])
            )
            extra = _stringList(extraValue, f"{prefix}.extraArgs")
            _rejectFirstClassArgs(extra, f"{prefix}.extraArgs")
            models[name] = LlamaCppModel(
                name=name,
                modelPath=modelPath,
                mmprojPath=mmprojPath,
                contextWindowTokens=_optionalPositiveInt(
                    self._modelValue(raw, "contextWindowTokens"),
                    f"{prefix}.contextWindowTokens",
                ),
                gpuLayers=_optionalNonNegativeInt(
                    self._modelValue(raw, "gpuLayers"), f"{prefix}.gpuLayers"
                ),
                threads=_optionalPositiveInt(
                    self._modelValue(raw, "threads"), f"{prefix}.threads"
                ),
                threadsBatch=_optionalPositiveInt(
                    self._modelValue(raw, "threadsBatch"), f"{prefix}.threadsBatch"
                ),
                batchSize=_optionalPositiveInt(
                    self._modelValue(raw, "batchSize"), f"{prefix}.batchSize"
                ),
                ubatchSize=_optionalPositiveInt(
                    self._modelValue(raw, "ubatchSize"), f"{prefix}.ubatchSize"
                ),
                unifiedKv=_optionalBool(
                    self._modelValue(raw, "unifiedKv"), f"{prefix}.unifiedKv"
                ),
                kvOffload=_optionalBool(
                    self._modelValue(raw, "kvOffload"), f"{prefix}.kvOffload"
                ),
                cacheTypeK=_optionalString(
                    self._modelValue(raw, "cacheTypeK"), f"{prefix}.cacheTypeK"
                ),
                cacheTypeV=_optionalString(
                    self._modelValue(raw, "cacheTypeV"), f"{prefix}.cacheTypeV"
                ),
                flashAttention=_optionalBool(
                    self._modelValue(raw, "flashAttention"), f"{prefix}.flashAttention"
                ),
                cpuMoeLayers=_optionalNonNegativeInt(
                    self._modelValue(raw, "cpuMoeLayers"), f"{prefix}.cpuMoeLayers"
                ),
                loadMode=_optionalLoadMode(
                    self._modelValue(raw, "loadMode"), f"{prefix}.loadMode"
                ),
                parallelSlots=_positiveInt(parallelValue, f"{prefix}.parallelSlots"),
                extraArgs=extra,
            )
        return models

    def start(self) -> None:
        """Starts the configured default model when Actant owns the server."""
        if self.manageServer:
            self.ensureModel(self.defaultModel)

    def ensureModel(self, model: str | None) -> LlamaCppModel | None:
        """Ensures the requested managed model is resident and returns its profile."""
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
        if (
            self.activeModelName == selectedName
            and self.process is not None
            and self.process.poll() is None
        ):
            return selected
        self.stop()
        self._startModel(selected)
        return selected

    def _serverArgs(self, model: LlamaCppModel) -> list[str]:
        """Materializes the exact llama-server argv for one effective model profile."""
        if self.executable is None:
            raise RuntimeError("Managed llama.cpp executable is unavailable.")
        args = [
            str(self.executable), "-m", str(model.modelPath), "--host", self.host,
            "--port", str(self.port), "--parallel", str(model.parallelSlots),
        ]
        pairs = (
            ("-c", model.contextWindowTokens), ("-ngl", model.gpuLayers),
            ("-t", model.threads), ("--threads-batch", model.threadsBatch),
            ("--batch-size", model.batchSize), ("--ubatch-size", model.ubatchSize),
            ("--cache-type-k", model.cacheTypeK), ("--cache-type-v", model.cacheTypeV),
            ("--n-cpu-moe", model.cpuMoeLayers), ("--load-mode", model.loadMode),
        )
        for flag, value in pairs:
            if value is not None:
                args += [flag, str(value)]
        if model.unifiedKv is True:
            args.append("--kv-unified")
        # False is llama.cpp's default, so omission preserves the requested state.
        if model.kvOffload is not None:
            args.append("--kv-offload" if model.kvOffload else "--no-kv-offload")
        if model.flashAttention is not None:
            args += ["--flash-attn", "on" if model.flashAttention else "off"]
        if model.mmprojPath is not None:
            args += ["--mmproj", str(model.mmprojPath)]
        args.extend(model.extraArgs)
        return args

    def _startModel(self, model: LlamaCppModel) -> None:
        """Starts one managed llama-server and waits until its health endpoint is ready."""
        args = self._serverArgs(model)
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL)
        self.activeModelName = model.name
        try:
            timeoutValue = self.config.get("startupTimeoutSeconds", 120.0)
            if type(timeoutValue) not in {int, float}:
                raise ValueError("llamaCpp.startupTimeoutSeconds must be numeric.")
            self._waitReady(float(timeoutValue))
        except Exception:
            self.stop()
            raise

    def _waitReady(self, timeoutSeconds: float) -> None:
        """Waits for managed-server health or raises on timeout/early process exit."""
        if not math.isfinite(timeoutSeconds) or timeoutSeconds <= 0:
            raise ValueError(
                "llamaCpp.startupTimeoutSeconds must be a positive finite number."
            )
        deadline = time.monotonic() + timeoutSeconds
        health = f"{self.baseUrl}/health"
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(
                    "llama-server exited during startup with code "
                    f"{self.process.returncode}."
                )
            try:
                with urlRequest.urlopen(health, timeout=1.0) as response:
                    if 200 <= response.status < 300:
                        return
            except (URLError, HTTPError, TimeoutError):
                pass
            time.sleep(0.2)
        raise TimeoutError(
            f"llama-server did not become ready within {timeoutSeconds} seconds."
        )

    def postJson(
        self,
        endpoint: str,
        payload: dict[str, object],
        *,
        timeoutSeconds: float,
    ) -> dict[str, object]:
        """Posts one JSON request to llama.cpp and requires an object response."""
        if type(endpoint) is not str or not endpoint.startswith("/"):
            raise ValueError("llama.cpp endpoint must be an absolute HTTP path.")
        if not math.isfinite(timeoutSeconds) or timeoutSeconds <= 0:
            raise ValueError("llama.cpp HTTP timeout must be a positive finite number.")
        request = urlRequest.Request(
            f"{self.baseUrl}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlRequest.urlopen(request, timeout=timeoutSeconds) as response:
                raw = response.read()
        except HTTPError as err:
            raise LlmProviderConnectionError(
                f"llama.cpp returned HTTP {err.code} for {endpoint}."
            ) from err
        except (URLError, TimeoutError) as err:
            raise LlmProviderConnectionError(
                f"Failed communicating with llama.cpp at {endpoint}."
            ) from err
        try:
            decoded = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise LlmProviderProtocolError(
                f"llama.cpp returned invalid JSON from {endpoint}."
            ) from err
        if not isinstance(decoded, dict):
            raise LlmProviderProtocolError(
                f"llama.cpp returned a non-object JSON response from {endpoint}."
            )
        return decoded

    def stop(self) -> None:
        """Stops the managed server, escalating to kill after the grace period."""
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
    """Creates the shared driver, starts it, and registers the llama.cpp provider."""
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
    """Stops a driver created by onLoad while tolerating foreign lifecycle state."""
    del ctx
    if isinstance(driver, LlamaCppDriver):
        driver.stop()
