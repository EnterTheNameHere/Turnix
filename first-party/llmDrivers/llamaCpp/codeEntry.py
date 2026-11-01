# file: first-party/llmDrivers/llamaCpp/codeEntry.py ; version: 8
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import time
import urllib.request as urlRequest
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
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


def _loadConfiguration() -> ModuleType:
    """Load the provider-local configuration helper independently of Pack import paths."""
    name = "actantLlamaCppConfiguration"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("configuration.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load llama.cpp configuration support.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


_configuration = _loadConfiguration()


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
    """Frozen requested launch settings, not proof of model/backend effective behavior."""

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
    startup: Mapping[str, ImmutableValue]
    requested: Mapping[str, ImmutableValue]
    inputs: tuple[ImmutableValue, ...]

    def launchProfile(self) -> dict[str, ImmutableValue]:
        """Project only allowlisted requested scalars for automatic metadata.

        Paths, arbitrary strings, kwargs and extraArgs belong to the driver's
        private configurationEvidence accessor. This projection proves no honor.
        """
        profile: dict[str, ImmutableValue] = {
            "parallelSlots": self.parallelSlots,
            "semantics": "requested-not-effective",
        }
        optional = {
            "contextWindowTokens": self.contextWindowTokens,
            "gpuLayers": self.gpuLayers,
            "threads": self.threads,
            "threadsBatch": self.threadsBatch,
            "batchSize": self.batchSize,
            "ubatchSize": self.ubatchSize,
            "unifiedKv": self.unifiedKv,
            "kvOffload": self.kvOffload,
            "flashAttention": self.flashAttention,
            "cpuMoeLayers": self.cpuMoeLayers,
            "loadMode": self.loadMode,
        }
        profile.update({key: value for key, value in optional.items() if value is not None})
        for key in ("moeCacheMiB", "cpuMoe", "serverReasoning", "serverReasoningBudget",
                    "serverReasoningFormat", "serverReasoningPreserve"):
            if self.startup[key] is not None:
                profile[key] = self.startup[key]
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
            "timeoutSeconds": options.timeoutSeconds,
            "managedServer": self.driver.manageServer,
            "configurationEvidence": self.driver.configurationSummary(model),
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
            ),
            metadata=metadata,
        )

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        """Streams one OpenAI-compatible chat-completions request from llama.cpp."""
        self.driver.ensureModel(request.model)
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
            raise LlmProviderConnectionError(
                f"llama.cpp returned HTTP {err.code} for chat completions."
            ) from None
        except (URLError, TimeoutError):
            raise LlmProviderConnectionError(
                "Failed communicating with llama.cpp for chat completions."
            ) from None


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
    if not isinstance(value, (list, tuple)) or not all(type(item) is str for item in value):
        raise ValueError(f"{name} must be a list of strings.")
    return tuple(value)


def _rejectFirstClassArgs(extraArgs: tuple[str, ...], name: str) -> None:
    """Rejects escape-hatch flags whose authority belongs to structured fields."""
    _configuration.inspectExtraArgs(extraArgs, name)


class LlamaCppDriver:
    """Long-lived CodeEntry-owned llama-server and model-residency manager."""

    def __init__(self, config: Mapping[str, object]) -> None:
        """Validates driver configuration without starting a managed server."""
        self._prepared = _configuration.prepare(config)
        self.config = self._prepared.values
        manageValue = self.config.get("manageServer", False)
        if type(manageValue) is not bool:
            raise TypeError("llamaCpp.manageServer must be a boolean.")
        self.manageServer = manageValue
        timeout = self.config.get("startupTimeoutSeconds", 120.0)
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("llamaCpp.startupTimeoutSeconds must be a positive finite number.")
        self.startupTimeoutSeconds = float(timeout)
        self.capabilityCheck = self.config.get("capabilityCheck", "none")
        if self.capabilityCheck not in ("none", "required"):
            raise ValueError("llamaCpp.capabilityCheck must be none or required.")
        if not self.manageServer:
            if self.capabilityCheck != "none":
                raise ValueError("External server capabilities cannot be certified by a local executable.")
            sources = [self.config]
            rawModels = self.config.get("models")
            if isinstance(rawModels, Mapping):
                sources.extend(raw for raw in rawModels.values() if isinstance(raw, Mapping))
            if any(source.get(key) is not None for source in sources for key in _configuration.NEW_FIELDS):
                raise ValueError("New startup controls require managed mode; configure the external server separately.")
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
                "the configured host/port."
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
            self.executable = self._prepared.resolvePath(executableValue)
            if not self.executable.is_file():
                raise FileNotFoundError(
                    "llama-server executable does not exist."
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
        self._launches: dict[str, object] = {}
        self._launchGeneration = 0
        self._buildEvidence = _configuration.freeze({"status": "unverified" if self.manageServer else "external-unverified",
            "sourceCommit": _configuration.SOURCE_COMMIT})

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
        if not isinstance(source, Mapping):
            raise ValueError("llamaCpp.models must be an object keyed by model name.")
        models: dict[str, LlamaCppModel] = {}
        for name, raw in source.items():
            if type(name) is not str or not name:
                raise ValueError("llamaCpp.models keys must be non-empty strings.")
            if not isinstance(raw, Mapping):
                raise ValueError("llamaCpp.models entries must be objects.")
            prefix = "llamaCpp.models entry"
            modelPathValue = raw.get("modelPath")
            if type(modelPathValue) is not str or not modelPathValue:
                raise ValueError(f"{prefix}.modelPath is required.")
            modelPath = self._prepared.resolvePath(modelPathValue)
            if self.manageServer and not modelPath.is_file():
                raise FileNotFoundError("llama.cpp model does not exist.")
            mmprojValue = self._modelValue(raw, "mmprojPath")
            mmprojPath: Path | None = None
            if mmprojValue is not None:
                if type(mmprojValue) is not str or not mmprojValue:
                    raise ValueError(f"{prefix}.mmprojPath must be a non-empty string.")
                mmprojPath = self._prepared.resolvePath(mmprojValue)
                if self.manageServer and not mmprojPath.is_file():
                    raise FileNotFoundError("llama.cpp mmproj does not exist.")
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
            fields = (
                "modelPath", "mmprojPath", "contextWindowTokens", "gpuLayers", "threads",
                "threadsBatch", "batchSize", "ubatchSize", "unifiedKv", "kvOffload",
                "cacheTypeK", "cacheTypeV", "flashAttention", "cpuMoeLayers", "loadMode",
                "parallelSlots", "extraArgs", *_configuration.NEW_FIELDS,
            )
            resolved = {key: self._modelValue(raw, key) for key in fields}
            resolved.update(modelPath=modelPathValue, host=self.host, port=self.port,
                            parallelSlots=parallelValue, extraArgs=extra)
            startup = _configuration.parseStartup(resolved)
            if self.manageServer:
                _configuration.validateEnvironment(self._prepared.environment, resolved, extra)
            else:
                selectedExtra, _unknown = _configuration.inspectExtraArgs(extra)
                if selectedExtra.get("--jinja") == "--no-jinja" and any(startup.values()):
                    raise ValueError("Template controls require enabled Jinja.")
            provenance = {key: {"value": resolved[key], "source": "model" if key in raw else
                "driver" if key in self.config else "omitted",
                "specified": key in raw or key in self.config} for key in fields}
            inputs = [{"role": "target", "configuredPath": modelPathValue,
                       "resolvedPath": str(modelPath), "contentSha256": None}]
            if mmprojPath is not None:
                inputs.append({"role": "mmproj", "configuredPath": mmprojValue,
                               "resolvedPath": str(mmprojPath), "contentSha256": None})
            selectedExtra, _unknown = _configuration.inspectExtraArgs(extra)
            template = selectedExtra.get("--chat-template-file")
            if template is None and self.manageServer:
                template = self._prepared.environment["LLAMA_ARG_CHAT_TEMPLATE_FILE"]
            if template is not None:
                inputs.append({"role": "template", "configuredPath": template,
                               "resolvedPath": str(self._prepared.resolvePath(template)), "contentSha256": None})
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
                startup=startup,
                requested=_configuration.freeze(provenance),
                inputs=_configuration.freeze(inputs),
            )
            if models[name].cpuMoeLayers is not None and models[name].cpuMoeLayers > 2147483647:
                raise ValueError("cpuMoeLayers must fit a signed 32-bit integer.")
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
        except KeyError:
            raise LookupError("llama.cpp model is not configured.") from None
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
        elif model.unifiedKv is False:
            args.append("--no-kv-unified")
        if model.kvOffload is not None:
            args.append("--kv-offload" if model.kvOffload else "--no-kv-offload")
        if model.flashAttention is not None:
            args += ["--flash-attn", "on" if model.flashAttention else "off"]
        if model.mmprojPath is not None:
            args += ["--mmproj", str(model.mmprojPath)]
        args.extend(_configuration.startupArgs(model.startup))
        args.extend(model.extraArgs)
        return args

    def _selectedModel(self, model: str | None) -> LlamaCppModel | None:
        """Resolve a configured model for evidence without acquiring residency."""
        if not self.models:
            return None
        try:
            return self.models[self.defaultModel if model is None else model]
        except KeyError:
            raise LookupError("llama.cpp model is not configured.") from None

    def checkCapabilities(self, model: str | None = None, *, refresh: bool = False) -> Mapping[str, ImmutableValue]:
        """Explicitly probe advertised pinned startup surface without starting a model.

        Results contain private executable/help evidence. A successful result proves
        only advertised flags and a reported source prefix, never effective honor.
        """
        if not self.manageServer or self.executable is None:
            raise ValueError("A local probe cannot certify an external server.")
        selected = self._selectedModel(model)
        result = dict(_configuration.probeCapabilities(self.executable, refresh=refresh))
        required = []
        if selected is not None:
            args = self._serverArgs(selected)
            if selected.extraArgs:
                args = args[:-len(selected.extraArgs)]
            index = 1
            byFlag = {flag: item for item in _configuration.OPTIONS for flag in item[0]}
            while index < len(args):
                flag = args[index]
                aliases, arity, _owner, _environment = byFlag[flag]
                longFlag = flag if flag.startswith("--") else next(alias for alias in aliases if alias.startswith("--"))
                required.append(longFlag)
                index += 1 + arity
        for aliases, _arity, _owner, environmentName in _configuration.OPTIONS:
            if environmentName is None:
                continue
            negativeName = _configuration.NEGATIVE_ENV.get(environmentName)
            hasNegative = negativeName is not None and self._prepared.environment[negativeName] is not None
            if hasNegative or self._prepared.environment[environmentName] is not None:
                longFlags = [flag for flag in aliases if flag.startswith("--")]
                selectedFlag = (
                    next((flag for flag in longFlags if flag.startswith("--no-")), longFlags[0])
                    if hasNegative else longFlags[0]
                )
                required.append(selectedFlag)
        missing = sorted(set(required) - set(result.get("advertisedFlags", ())))
        result.update(requiredFlags=tuple(required), missingFlags=tuple(missing))
        if result["status"] == "advertised-pinned-surface" and missing:
            result["status"] = "missing-flags"
        self._buildEvidence = _configuration.freeze(result)
        return self._buildEvidence

    def configurationEvidence(self, model: str | None = None) -> Mapping[str, ImmutableValue]:
        """Return a private immutable configuration snapshot without probing or startup.

        Contains paths, complete argv, arbitrary kwargs, budget messages and possibly
        credentials supplied as extraArgs. Callers must explicitly select retention;
        do not indiscriminately log this value or attach it to generic metadata.
        Paths identify inputs, not their contents. Unknown effective behavior remains
        separate from requested configuration and concrete launch evidence.
        """
        selected = self._selectedModel(model)
        requested = {} if selected is None else selected.requested
        inputs = () if selected is None else selected.inputs
        profileId = _configuration.identity({"requested": requested, "inputs": inputs,
            "executable": None if self.executable is None else str(self.executable),
            "environment": self._prepared.environment if self.manageServer else {},
            "managed": self.manageServer, "baseUrl": self.baseUrl,
            "capabilityCheck": self.capabilityCheck,
            "workingDirectory": self._prepared.workingDirectory if self.manageServer else None})
        launch = {"status": "not-launched" if self.manageServer else "external-unapplied",
                  "actualArgv": None, "generation": None}
        if selected is not None:
            launch.update(self._launches.get(selected.name, {}))
        launch["plannedArgv"] = tuple(self._serverArgs(selected)) if self.manageServer and selected is not None else None
        return _configuration.freeze({"schemaId": _configuration.SCHEMA, "profileId": profileId,
            "mode": "managed" if self.manageServer else "external",
            "requested": requested, "inputs": inputs, "launch": launch,
            "relevantEnvironment": self._prepared.environment if self.manageServer else {},
            "build": self._buildEvidence, "configuredExecutable": None if self.executable is None else str(self.executable),
            "userReportedBuild": {"version": "0.8.0-dev", "build": 12031, "commit": "060b2fea4",
                "provenance": "assignment-user-report-not-binary-verification"},
            "providerReportedEffective": {"status": "unknown-unqueried"},
            "extraArgsCertification": "uncertified" if selected is not None and selected.extraArgs else "not-used"})

    def configurationSummary(self, model: str | None = None) -> Mapping[str, ImmutableValue]:
        """Project safe evidence status/identity without paths, argv or arbitrary strings."""
        evidence = self.configurationEvidence(model)
        return _configuration.freeze({key: evidence[key] for key in ("schemaId", "profileId", "mode")} | {
            "buildStatus": evidence["build"]["status"],
            "launchStatus": evidence["launch"]["status"],
            "effectiveBehavior": "unverified", "extraArgsCertification": evidence["extraArgsCertification"]})

    def _startModel(self, model: LlamaCppModel) -> None:
        """Starts one managed llama-server and waits until its health endpoint is ready."""
        args = self._serverArgs(model)
        if self.capabilityCheck == "required":
            result = self.checkCapabilities(model.name)
            if result["status"] != "advertised-pinned-surface":
                raise ValueError("Required llama.cpp capability check failed; inspect private configuration evidence.")
        else:
            observed = _configuration.executableIdentity(self.executable)
            previous = dict(self._buildEvidence)
            if previous.get("executable") != observed:
                previous = {"status": "unverified", "sourceCommit": _configuration.SOURCE_COMMIT}
            self._buildEvidence = _configuration.freeze(previous | {"executable": observed})
        environment = self._prepared.launchEnvironment()
        self._launchGeneration += 1
        launch = {"generation": self._launchGeneration, "status": "launch-failed", "actualArgv": None,
                  "environment": self._prepared.environment, "build": self._buildEvidence,
                  "workingDirectory": self._prepared.workingDirectory}
        self._launches[model.name] = _configuration.freeze(launch)
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, env=environment,
                                            cwd=self._prepared.workingDirectory)
            launch.update(status="started", actualArgv=tuple(args))
            self._launches[model.name] = _configuration.freeze(launch)
            self.activeModelName = model.name
            self._waitReady(self.startupTimeoutSeconds)
            launch["status"] = "ready"
            self._launches[model.name] = _configuration.freeze(launch)
        except Exception:
            launch["status"] = "launch-failed" if launch["actualArgv"] is None else "startup-failed"
            self._launches[model.name] = _configuration.freeze(launch)
            self.stop()
            raise LlmProviderConnectionError("Managed llama.cpp startup failed.") from None

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
                f"llama.cpp returned HTTP {err.code} for a JSON operation."
            ) from None
        except (URLError, TimeoutError):
            raise LlmProviderConnectionError(
                "Failed communicating with llama.cpp for a JSON operation."
            ) from None
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
        if self.activeModelName is not None and self.activeModelName in self._launches:
            launch = dict(self._launches[self.activeModelName])
            if launch["status"] == "ready":
                launch["status"] = "stopped"
                self._launches[self.activeModelName] = _configuration.freeze(launch)
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
