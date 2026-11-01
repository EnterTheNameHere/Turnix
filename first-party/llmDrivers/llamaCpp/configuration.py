# file: first-party/llmDrivers/llamaCpp/configuration.py ; version: 2
"""Provider-local startup authority, immutable inputs and bounded build evidence."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping
from pathlib import Path

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer

SOURCE_COMMIT = "060b2fea4ed528810ddc91d48fb22b3e4d63eb85"
SCHEMA = "actant.llamaCpp.configuration@1"
LC01_FIELDS = (
    "moeCacheMiB", "cpuMoe", "serverReasoning", "serverReasoningEffort",
    "serverReasoningBudget", "serverReasoningBudgetMessage", "serverReasoningFormat",
    "serverReasoningPreserve", "chatTemplateKwargs",
)
SPEC_FIELDS = (
    "specMethods", "draftMaxTokens", "draftMinTokens", "draftUbatchSize", "draftModelPath",
    "draftDevices", "draftGpuLayers", "draftSampling", "draftBackendSampling", "draftPMin", "draftPSplit",
)
NEW_FIELDS = LC01_FIELDS + SPEC_FIELDS
# common_speculative_init uses this priority, not the order of CLI tokens.
SPEC_PRIORITY = ("ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod", "ngram-cache",
                 "draft-simple", "draft-eagle3", "draft-mtp", "draft-dflash", "draft-dspark")
SPEC_OPTIONS = (
    (("--spec-type",), 1, "specMethods", "LLAMA_ARG_SPEC_TYPE"),
    (("--spec-draft-n-max",), 1, "draftMaxTokens", "LLAMA_ARG_SPEC_DRAFT_N_MAX"),
    (("--spec-draft-n-min",), 1, "draftMinTokens", "LLAMA_ARG_SPEC_DRAFT_N_MIN"),
    (("--spec-draft-n-ubatch",), 1, "draftUbatchSize", "LLAMA_ARG_SPEC_DRAFT_N_UBATCH"),
    (("--spec-draft-model", "-md", "--model-draft"), 1, "draftModelPath", "LLAMA_ARG_SPEC_DRAFT_MODEL"),
    (("--spec-draft-device", "-devd", "--device-draft"), 1, "draftDevices", None),
    (("--spec-draft-ngl", "-ngld", "--gpu-layers-draft", "--n-gpu-layers-draft"), 1, "draftGpuLayers", "LLAMA_ARG_N_GPU_LAYERS_DRAFT"),
    (("--spec-draft-sampling",), 1, "draftSampling", "LLAMA_ARG_SPEC_DRAFT_SAMPLING"),
    (("--spec-draft-backend-sampling", "--no-spec-draft-backend-sampling"), 0, "draftBackendSampling", "LLAMA_ARG_SPEC_DRAFT_BACKEND_SAMPLING"),
    (("--spec-draft-p-min", "--draft-p-min"), 1, "draftPMin", "LLAMA_ARG_SPEC_DRAFT_P_MIN"),
    (("--spec-draft-p-split", "--draft-p-split"), 1, "draftPSplit", "LLAMA_ARG_SPEC_DRAFT_P_SPLIT"),
)
# Recognized deferred controls retain argv arity, but are never certified by validation.
SPEC_EXTRAS = (
    (("--spec-draft-threads", "-td", "--threads-draft"), 1, None, None),
    (("--spec-draft-threads-batch", "-tbd", "--threads-batch-draft"), 1, None, None),
    (("--spec-draft-cpu-mask", "-Cd", "--cpu-mask-draft"), 1, None, None),
    (("--spec-draft-cpu-range", "-Crd", "--cpu-range-draft"), 1, None, None),
    (("--spec-draft-cpu-strict", "--cpu-strict-draft"), 1, None, None),
    (("--spec-draft-prio", "--prio-draft"), 1, None, None),
    (("--spec-draft-poll", "--poll-draft"), 1, None, None),
    (("--spec-draft-cpu-mask-batch", "-Cbd", "--cpu-mask-batch-draft"), 1, None, None),
    (("--spec-draft-cpu-range-batch", "-Crbd", "--cpu-range-batch-draft"), 1, None, None),
    (("--spec-draft-cpu-strict-batch", "--cpu-strict-batch-draft"), 1, None, None),
    (("--spec-draft-prio-batch", "--prio-batch-draft"), 1, None, None),
    (("--spec-draft-poll-batch", "--poll-batch-draft"), 1, None, None),
    (("--spec-draft-type-k", "-ctkd", "--cache-type-k-draft"), 1, None, "LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K"),
    (("--spec-draft-type-v", "-ctvd", "--cache-type-v-draft"), 1, None, "LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_V"),
    (("--spec-draft-override-tensor", "-otd", "--override-tensor-draft"), 1, None, None),
    (("--spec-draft-cpu-moe", "-cmoed", "--cpu-moe-draft"), 0, None, "LLAMA_ARG_SPEC_DRAFT_CPU_MOE"),
    (("--spec-draft-n-cpu-moe", "--spec-draft-ncmoe", "-ncmoed", "--n-cpu-moe-draft"), 1, None, "LLAMA_ARG_SPEC_DRAFT_N_CPU_MOE"),
    (("--lookup-cache-static", "-lcs"), 1, None, None),
    (("--lookup-cache-dynamic", "-lcd"), 1, None, None),
)
SPEC_EXTRAS += tuple(((f"--spec-{method}-{suffix}",), 1, None, None)
                     for method in ("ngram-simple", "ngram-map-k", "ngram-map-k4v")
                     for suffix in ("size-n", "size-m", "min-hits"))
SPEC_EXTRAS += tuple(((f"--spec-ngram-mod-{suffix}",), 1, None, None)
                     for suffix in ("n-min", "n-max", "n-match"))
SPEC_EXTRAS += (
    (("--spec-synth-len",), 1, None, "LLAMA_ARG_SPEC_SYNTH_LEN"),
    (("--spec-synth-rates",), 1, None, "LLAMA_ARG_SPEC_SYNTH_RATES"),
)
# Pinned common/arg.cpp: aliases, arity, owning field and actual set_env spelling.
# None owner means a recognized experimental escape hatch, not certified support.
OPTIONS = (
    (("-m", "--model"), 1, "modelPath", "LLAMA_ARG_MODEL"),
    (("--host",), 1, "host", "LLAMA_ARG_HOST"),
    (("--port",), 1, "port", "LLAMA_ARG_PORT"),
    (("-np", "--parallel"), 1, "parallelSlots", "LLAMA_ARG_N_PARALLEL"),
    (("-c", "--ctx-size"), 1, "contextWindowTokens", "LLAMA_ARG_CTX_SIZE"),
    (("-ngl", "--gpu-layers", "--n-gpu-layers"), 1, "gpuLayers", "LLAMA_ARG_N_GPU_LAYERS"),
    (("-t", "--threads"), 1, "threads", "LLAMA_ARG_THREADS"),
    (("-tb", "--threads-batch"), 1, "threadsBatch", None),
    (("-b", "--batch-size"), 1, "batchSize", "LLAMA_ARG_BATCH"),
    (("-ub", "--ubatch-size"), 1, "ubatchSize", "LLAMA_ARG_UBATCH"),
    (("-kvu", "--kv-unified", "-no-kvu", "--no-kv-unified"), 0, "unifiedKv", "LLAMA_ARG_KV_UNIFIED"),
    (("-kvo", "--kv-offload", "-nkvo", "--no-kv-offload"), 0, "kvOffload", "LLAMA_ARG_KV_OFFLOAD"),
    (("-ctk", "--cache-type-k"), 1, "cacheTypeK", "LLAMA_ARG_CACHE_TYPE_K"),
    (("-ctv", "--cache-type-v"), 1, "cacheTypeV", "LLAMA_ARG_CACHE_TYPE_V"),
    (("-fa", "--flash-attn"), 1, "flashAttention", "LLAMA_ARG_FLASH_ATTN"),
    (("-lm", "--load-mode"), 1, "loadMode", "LLAMA_ARG_LOAD_MODE"),
    (("-mm", "--mmproj"), 1, "mmprojPath", "LLAMA_ARG_MMPROJ"),
    (("-cmoe", "--cpu-moe"), 0, "cpuMoe", "LLAMA_ARG_CPU_MOE"),
    (("-ncmoe", "--n-cpu-moe"), 1, "cpuMoeLayers", "LLAMA_ARG_N_CPU_MOE"),
    (("--moe-cache-mib",), 1, "moeCacheMiB", "LLAMA_ARG_MOE_CACHE_MIB"),
    (("-rea", "--reasoning"), 1, "serverReasoning", "LLAMA_ARG_REASONING"),
    (("--reasoning-effort",), 1, "serverReasoningEffort", "LLAMA_ARG_REASONING_EFFORT"),
    (("--reasoning-budget",), 1, "serverReasoningBudget", "LLAMA_ARG_THINK_BUDGET"),
    (("--reasoning-budget-message",), 1, "serverReasoningBudgetMessage", "LLAMA_ARG_THINK_BUDGET_MESSAGE"),
    (("--reasoning-format",), 1, "serverReasoningFormat", "LLAMA_ARG_THINK"),
    (("--reasoning-preserve", "--no-reasoning-preserve"), 0, "serverReasoningPreserve", "LLAMA_ARG_REASONING_PRESERVE"),
    (("--chat-template-kwargs",), 1, "chatTemplateKwargs", "LLAMA_ARG_CHAT_TEMPLATE_KWARGS"),
    (("--chat-template",), 1, None, "LLAMA_ARG_CHAT_TEMPLATE"),
    (("--chat-template-file",), 1, None, "LLAMA_ARG_CHAT_TEMPLATE_FILE"),
    (("--jinja", "--no-jinja"), 0, None, "LLAMA_ARG_JINJA"),
    (("--grammar",), 1, None, None),
    (("--grammar-file",), 1, None, None),
    (("-ot", "--override-tensor"), 1, None, "LLAMA_ARG_OVERRIDE_TENSOR"),
) + SPEC_OPTIONS + SPEC_EXTRAS
_BY_FLAG = {flag: item for item in OPTIONS for flag in item[0]}
_ALTERNATE_INPUTS = {
    "-mu", "--model-url", "-hf", "-hfr", "--hf-repo", "-hff", "--hf-file",
    "-dr", "--docker-repo", "-mmu", "--mmproj-url", "--mmproj-auto",
    "--no-mmproj", "--no-mmproj-auto", "--models-dir", "--models-preset",
    "--spec-draft-hf", "-hfd", "-hfrd", "--hf-repo-draft",
    "--spec-draft-url", "--model-url-draft", "--model-draft-url", "--hf-file-draft",
    "--spec-default", "--mtp", "--dflash", "--eagle3",
    "--gpt-oss-20b-default", "--gpt-oss-120b-default", "--vision-gemma-4b-default", "--vision-gemma-12b-default",
    "--embd-gemma-default", "--fim-qwen-1.5b-default", "--fim-qwen-3b-default",
    "--fim-qwen-7b-default", "--fim-qwen-30b-default",
    "--fim-qwen-7b-spec", "--fim-qwen-14b-spec",
}
_ALTERNATE_ENV = (
    "LLAMA_ARG_MODEL_URL", "LLAMA_ARG_HF_REPO", "LLAMA_ARG_HF_FILE",
    "LLAMA_ARG_DOCKER_REPO", "LLAMA_ARG_MMPROJ_URL", "LLAMA_ARG_MMPROJ_AUTO",
    "LLAMA_ARG_MODELS_DIR", "LLAMA_ARG_MODELS_PRESET",
    "LLAMA_ARG_SPEC_DRAFT_HF_REPO", "LLAMA_ARG_DRAFT_MAX", "LLAMA_ARG_DRAFT_MIN",
)
# common_arg::get_value_from_env/get_env also expose these legacy negative aliases.
# Their presence forces false regardless of their textual value.
NEGATIVE_ENV = {name: name.replace("LLAMA_ARG_", "LLAMA_ARG_NO_", 1) for name in (
    "LLAMA_ARG_KV_UNIFIED", "LLAMA_ARG_KV_OFFLOAD", "LLAMA_ARG_REASONING_PRESERVE",
    "LLAMA_ARG_JINJA", "LLAMA_ARG_MMPROJ_AUTO",
    "LLAMA_ARG_SPEC_DRAFT_BACKEND_SAMPLING",
)}
CONFIG_ENV = ("PROGRAMDATA", "APPDATA") if os.name == "nt" else ("XDG_CONFIG_HOME", "HOME")
ENV_NAMES = tuple(sorted({item[3] for item in OPTIONS if item[3]}
                         | set(_ALTERNATE_ENV) | set(NEGATIVE_ENV.values()) | set(CONFIG_ENV)))
_REMOVED = frozenset({
    "--draft", "--draft-n", "--draft-max", "--draft-min", "--draft-n-min",
    "--spec-ngram-size-n", "--spec-ngram-size-m", "--spec-ngram-min-hits",
})
_TEMPLATE_KEYS = {
    "enable_thinking": "serverReasoning", "reasoning_effort": "serverReasoningEffort",
    "preserve_reasoning": "serverReasoningPreserve",
}
_PROBE_CACHE: dict[str, Mapping[str, ImmutableValue]] = {}


def freeze(value: object) -> ImmutableValue:
    """Copy JSON-compatible evidence into recursively immutable values."""
    return ImmutableValueFreezer().freeze(value)


def jsonValue(value: object) -> object:
    """Convert an immutable snapshot to fresh JSON containers without aliases."""
    if isinstance(value, Mapping):
        return {key: jsonValue(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [jsonValue(item) for item in value]
    return value


def identity(value: object) -> str:
    """Hash canonical configuration data, never claiming file-content identity."""
    encoded = json.dumps(jsonValue(value), sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(SCHEMA.encode() + b"\0" + encoded).hexdigest()


class PreparedConfiguration(Mapping[str, object]):
    """Bind frozen configuration and relevant ambient inputs before lease acquisition."""

    def __init__(self, config: Mapping[str, object]) -> None:
        """Snapshot caller-owned JSON and relevant environment, including absence."""
        try:
            self.values = freeze(config)
        except (ValueError, TypeError):
            raise ValueError("llamaCpp configuration must contain finite JSON values only.") from None
        names = set(ENV_NAMES) | {name for name in os.environ if isSpeculationEnv(name)}
        self.environment = freeze({name: os.environ.get(name) for name in sorted(names)})
        self.workingDirectory = str(Path.cwd())
        self.automaticConfig = freeze(automaticConfig(self.environment, self.workingDirectory))

    def __getitem__(self, key: str) -> object:
        """Read a frozen configuration item through the ordinary Mapping contract."""
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate configuration keys without exposing mutable containers."""
        return iter(self.values)

    def __len__(self) -> int:
        """Return the count of configured top-level settings."""
        return len(self.values)

    def compatibilityKey(self) -> str:
        """Include relevant environment absence/value in the shared lease identity."""
        return identity({"config": self.values, "environment": self.environment,
                         "workingDirectory": self.workingDirectory, "automaticConfig": self.automaticConfig})

    def resolvePath(self, path: str) -> Path:
        """Resolve configured input paths against the captured launch directory."""
        return (Path(self.workingDirectory) / path).resolve()

    def launchEnvironment(self) -> dict[str, str]:
        """Overlay frozen owned values/absences on current non-owned inheritance."""
        environment = dict(os.environ)
        for name in tuple(environment):
            if isSpeculationEnv(name) and name not in self.environment:
                environment.pop(name)
        for name, value in self.environment.items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        return environment


def prepare(config: Mapping[str, object]) -> PreparedConfiguration:
    """Reuse a lease-bound snapshot or capture one for direct driver construction."""
    return config if isinstance(config, PreparedConfiguration) else PreparedConfiguration(config)


def isSpeculationEnv(name: str) -> bool:
    """Recognize pinned and future speculative inputs for frozen presence/absence handling."""
    return name.startswith(("LLAMA_ARG_SPEC_", "LLAMA_ARG_NO_SPEC_", "LLAMA_ARG_DRAFT_")) or (
        name.startswith("LLAMA_ARG_") and name.endswith("_DRAFT"))


def automaticConfig(environment: Mapping[str, str | None],
                    workingDirectory: str | None = None) -> tuple[dict[str, ImmutableValue], ...]:
    """Observe pinned system/user automatic config paths without reading private contents."""
    roots = []
    if os.name == "nt":
        roots.extend(Path(environment[name]) / "llama.cpp" for name in CONFIG_ENV if environment.get(name))
    else:
        roots.append(Path("/etc/llama.cpp"))
        userRoot = environment.get("XDG_CONFIG_HOME") or str(Path(environment.get("HOME") or str(Path.home())) / ".config")
        roots.append(Path(userRoot) / "llama.cpp")
    result = []
    for root in roots:
        path = (Path(workingDirectory or str(Path.cwd())) / root / "config.ini").resolve()
        try:
            path.stat()
            state = "present"
        except FileNotFoundError:
            state = "absent"
        except OSError:
            state = "unreadable"
        result.append({"path": str(path), "present": state != "absent", "state": state})
    return tuple(result)


def validateAutomaticConfig(prepared: PreparedConfiguration) -> None:
    """Fail closed for explicit speculation when automatic config could hide startup inputs."""
    if any(item["present"] for item in automaticConfig(prepared.environment, prepared.workingDirectory)):
        raise ValueError("Explicit speculation requires absent upstream automatic config.ini files; inspect private evidence.")


def parseStartup(value: Mapping[str, object]) -> Mapping[str, ImmutableValue]:
    """Validate new startup values, preserving omission and explicit off/defaults."""
    result = {key: value.get(key) for key in NEW_FIELDS}
    for key in ("moeCacheMiB", "serverReasoningBudget"):
        item = result[key]
        lower = -1 if key == "serverReasoningBudget" else 0
        if item is not None and (type(item) is not int or not lower <= item <= 2147483647):
            raise ValueError(f"{key} must be an exact integer in [{lower}, 2147483647].")
    for key in ("cpuMoe", "serverReasoningPreserve"):
        if result[key] is not None and type(result[key]) is not bool:
            raise ValueError(f"{key} must be a boolean.")
    enums = {"serverReasoning": ("on", "off", "auto"),
             "serverReasoningFormat": ("none", "auto", "deepseek", "deepseek-legacy")}
    for key, allowed in enums.items():
        if result[key] is not None and (type(result[key]) is not str or result[key] not in allowed):
            raise ValueError(f"{key} must be one of {', '.join(allowed)}.")
    for key in ("serverReasoningEffort", "serverReasoningBudgetMessage"):
        item = result[key]
        if item is not None and (type(item) is not str or (key.endswith("Effort") and not item.strip())):
            raise ValueError(f"{key} must be an exact string with a valid length.")
    kwargs = result["chatTemplateKwargs"]
    if kwargs is not None:
        validateKwargs(kwargs)
        for key, field in _TEMPLATE_KEYS.items():
            if key in kwargs and result[field] is not None:
                raise ValueError(f"Duplicate authority for {field} in chatTemplateKwargs.")
    if result["cpuMoe"] is True and value.get("cpuMoeLayers") is not None:
        raise ValueError("cpuMoe and cpuMoeLayers cannot both select expert placement.")
    result.update(parseSpeculation(value))
    return freeze(result)


def parseSpeculation(value: Mapping[str, object]) -> dict[str, ImmutableValue]:
    """Validate the documented structured subset without enabling any omitted method.

    The pinned parser accepts wider integer/probability inputs in several places;
    this surface rejects negative minimum counts and probabilities outside [0, 1]
    to avoid size_t casts or meaningless probability thresholds. GPU layer counts
    retain the parser's signed integer semantics plus explicit auto/all strings.
    """
    result = {key: value.get(key) for key in SPEC_FIELDS}
    methods = result["specMethods"]
    if methods is not None:
        if not isinstance(methods, (list, tuple)) or not methods:
            raise ValueError("specMethods must be a nonempty ordered list.")
        if any(type(method) is not str or method not in (*SPEC_PRIORITY, "none") for method in methods):
            raise ValueError("specMethods contains an unsupported method.")
        if len(set(methods)) != len(methods) or ("none" in methods and len(methods) != 1):
            raise ValueError("specMethods cannot repeat methods or mix none with active methods.")
        methods = tuple(methods)
        if methods != ("none",) and methods != tuple(method for method in SPEC_PRIORITY if method in methods):
            raise ValueError("specMethods order must match pinned upstream priority; custom priority is not honored.")
        result["specMethods"] = methods
    for key in ("draftMaxTokens", "draftMinTokens", "draftUbatchSize"):
        item = result[key]
        if item is not None and (type(item) is not int or not 0 <= item <= 2147483647):
            raise ValueError(f"{key} must be a nonnegative signed 32-bit exact integer.")
    path = result["draftModelPath"]
    if path is not None and (type(path) is not str or not path.strip()):
        raise ValueError("draftModelPath must identify a nonblank local file path.")
    devices = result["draftDevices"]
    if devices is not None:
        if not isinstance(devices, (list, tuple)) or not devices or any(
            type(item) is not str or not item.strip() or "," in item for item in devices
        ):
            raise ValueError("draftDevices must be a nonempty list of individual device identifiers.")
        if len(set(devices)) != len(devices) or ("none" in devices and len(devices) != 1):
            raise ValueError("draftDevices cannot repeat devices or mix none with device names.")
        result["draftDevices"] = tuple(devices)
    layers = result["draftGpuLayers"]
    if layers is not None and not (
        (type(layers) is int and -2147483648 <= layers <= 2147483647)
        or (type(layers) is str and layers in {"auto", "all"})
    ):
        raise ValueError("draftGpuLayers must be a signed 32-bit exact integer, auto or all.")
    sampling = result["draftSampling"]
    if sampling is not None and (type(sampling) is not str or sampling not in {"greedy", "probabilistic"}):
        raise ValueError("draftSampling must be greedy or probabilistic.")
    backend = result["draftBackendSampling"]
    if backend is not None and type(backend) is not bool:
        raise ValueError("draftBackendSampling must be an exact boolean.")
    for key in ("draftPMin", "draftPSplit"):
        item = result[key]
        if item is not None and (type(item) not in {int, float} or not 0 <= item <= 1 or not math.isfinite(item)):
            raise ValueError(f"{key} must be a finite probability in [0, 1].")
    return result


def _inheritedSpecValue(field: str, value: str) -> object:
    """Decode pinned environment syntax before the same structured validation and gate."""
    if field == "specMethods":
        return value.split(",")
    if field in {"draftMaxTokens", "draftMinTokens", "draftUbatchSize"} or (
        field == "draftGpuLayers" and value not in {"auto", "all"}
    ):
        if not re.fullmatch(r"[+-]?\d+", value):
            raise ValueError("Inherited speculative integer is invalid.")
        return int(value)
    if field in {"draftPMin", "draftPSplit"}:
        try:
            return float(value)
        except ValueError:
            raise ValueError("Inherited speculative probability is invalid.") from None
    if field == "draftBackendSampling":
        if value not in {"on", "enabled", "true", "1", "off", "disabled", "false", "0"}:
            raise ValueError("Inherited draft backend sampling must be a boolean.")
        return value in {"on", "enabled", "true", "1"}
    return value


def validateSpeculation(resolved: Mapping[str, object], prepared: PreparedConfiguration,
                        extra: tuple[str, ...]) -> Mapping[str, ImmutableValue]:
    """Combine explicit methods, local auxiliary inputs and frozen inherited authorities.

    Inherited controls never silently select a method based on a model path. The
    disabled baseline excludes all draft/tuning inputs, speculative escape hatches,
    alternate selectors and automatic config files, independently of argv spelling.
    """
    effective = parseSpeculation(resolved)
    for _aliases, _arity, field, name in SPEC_OPTIONS:
        if name is not None:
            raw = prepared.environment[name]
            negative = NEGATIVE_ENV.get(name)
            if negative and prepared.environment[negative] is not None:
                raw = "0"
            if raw is not None:
                if effective[field] is not None:
                    raise ValueError("Inherited speculation duplicates structured startup authority.")
                effective[field] = _inheritedSpecValue(field, raw)
    effective = parseSpeculation(effective)
    selected, unknown = inspectExtraArgs(extra)
    deferred = {aliases[0] for aliases, _arity, _owner, _name in SPEC_EXTRAS}
    experimental = tuple(key for key in selected if key in deferred)
    unknownSpec = tuple(item for item in unknown if item.partition("=")[0].replace("_", "-").startswith(
        ("--spec-", "--draft-", "--model-draft", "--hf-repo-draft")))
    speculativeEnv = {name: item for name, item in prepared.environment.items()
                      if isSpeculationEnv(name) and item is not None}
    typedEnv = {name for _aliases, _arity, _owner, name in SPEC_OPTIONS if name}
    typedEnv.add("LLAMA_ARG_NO_SPEC_DRAFT_BACKEND_SAMPLING")
    deferredEnv = tuple(name for name in speculativeEnv if name not in typedEnv)
    methods = effective["specMethods"]
    configured = methods is not None or any(effective[key] is not None for key in SPEC_FIELDS[1:])
    configured = configured or bool(experimental or unknownSpec or speculativeEnv)
    if configured:
        validateAutomaticConfig(prepared)
    if methods is None:
        if configured:
            raise ValueError("Speculative inputs require explicit specMethods; auxiliary metadata must not enable drafting.")
        return freeze({"status": "omitted-unverified", "methods": None, "effectiveHonor": "unverified"})
    if methods == ("none",):
        if any(effective[key] is not None for key in SPEC_FIELDS[1:]) or experimental or unknownSpec or deferredEnv:
            raise ValueError("specMethods none conflicts with draft inputs or speculative tuning.")
        return freeze({"status": "explicit-none-requested", "methods": methods,
                       "effectiveHonor": "unverified", "automaticConfigPolicy": "require-absent"})
    hasDraft = any(method.startswith("draft-") for method in methods)
    if not hasDraft and any(effective[key] is not None for key in SPEC_FIELDS[1:]):
        raise ValueError("Draft controls require an explicit draft method, not only ngram methods.")
    needsAuxiliary = any(method in {"draft-simple", "draft-eagle3", "draft-dflash", "draft-dspark"} for method in methods)
    if needsAuxiliary and effective["draftModelPath"] is None:
        raise ValueError("Selected draft method requires an explicit local auxiliary model.")
    for name in experimental:
        if name.startswith("--spec-draft-") and not hasDraft:
            raise ValueError("Draft extraArgs require an explicit draft method.")
        for method in SPEC_PRIORITY:
            if name.startswith("--spec-" + method + "-") and method not in methods:
                raise ValueError("Ngram extraArgs require their matching speculative method.")
        if name.startswith("--lookup-cache-") and "ngram-cache" not in methods:
            raise ValueError("Lookup cache inputs require ngram-cache.")
    if not hasDraft and any("DRAFT" in name for name in deferredEnv):
        raise ValueError("Inherited draft tuning requires an explicit draft method.")
    minimum = 0 if effective["draftMinTokens"] is None else effective["draftMinTokens"]
    maximum = 3 if effective["draftMaxTokens"] is None else effective["draftMaxTokens"]
    if minimum > maximum:
        raise ValueError("draftMinTokens exceeds draftMaxTokens (pinned omitted defaults: 0 and 3).")
    return freeze({"status": "active-requested", "methods": methods,
                   "resolvedInputs": effective, "upstreamPriority": methods,
                   "uncertifiedExtraArgs": experimental + unknownSpec, "uncertifiedEnvironment": deferredEnv,
                   "effectiveHonor": "unverified", "automaticConfigPolicy": "require-absent"})


def validateKwargs(kwargs: object) -> None:
    """Require a finite JSON object and exact types for upstream reasoning keys."""
    if not isinstance(kwargs, Mapping):
        raise ValueError("chatTemplateKwargs must be a JSON object.")
    freeze(kwargs)
    for key in ("enable_thinking", "preserve_reasoning"):
        if key in kwargs and type(kwargs[key]) is not bool:
            raise ValueError(f"chatTemplateKwargs.{key} must be a boolean.")
    if "reasoning_effort" in kwargs:
        item = kwargs["reasoning_effort"]
        if type(item) is not str or not item.strip():
            raise ValueError("chatTemplateKwargs.reasoning_effort must be nonblank text.")


def inspectExtraArgs(arguments: tuple[str, ...], name: str = "extraArgs") -> tuple[dict[str, str], tuple[str, ...]]:
    """Walk known option arities; never reinterpret their dash-leading string values."""
    selected = {}
    unknown = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        flag, separator, attached = argument.partition("=")
        if flag.startswith("--"):
            flag = flag.replace("_", "-")  # pinned parser normalizes long-option underscores
        if flag in _REMOVED:
            raise ValueError(f"{name} contains a removed llama.cpp option.")
        if flag in _ALTERNATE_INPUTS:
            raise ValueError(f"{name} cannot replace the structured model/input authority.")
        option = _BY_FLAG.get(flag)
        if option is None:
            unknown.append(argument)
            index += 1
            continue
        aliases, arity, owner, _env = option
        if owner is not None:
            raise ValueError(f"{name} contains a first-class flag; use its structured setting.")
        family = aliases[0]
        if family in selected:
            raise ValueError(f"{name} contains duplicate option authority.")
        if not arity:
            if separator:
                raise ValueError(f"{name} gives a value to a switch.")
            selected[family] = flag
        elif separator:
            selected[family] = attached
        else:
            index += 1
            if index >= len(arguments):
                raise ValueError(f"{name} is missing an option value.")
            selected[family] = arguments[index]
        index += 1
    if "--chat-template" in selected and "--chat-template-file" in selected:
        raise ValueError("Only one chat template source may be configured.")
    return selected, tuple(unknown)


def validateEnvironment(environment: Mapping[str, str | None], resolved: Mapping[str, object],
                        extra: tuple[str, ...]) -> None:
    """Reject structured/inherited duplicate authority, including template-key aliases."""
    environment = dict(environment)
    if any(environment[name] is not None for name in ("LLAMA_ARG_DRAFT_MAX", "LLAMA_ARG_DRAFT_MIN")):
        raise ValueError("Inherited removed draft options are unsupported; use structured draft token controls.")
    for positive, negative in NEGATIVE_ENV.items():
        if environment[negative] is not None:
            if environment[positive] is not None:
                raise ValueError("Inherited positive and negative aliases duplicate startup authority.")
            environment[positive] = "0"
    selected, _unknown = inspectExtraArgs(extra)
    for name in _ALTERNATE_ENV:
        if environment[name] is not None:
            raise ValueError("Inherited model/input selector conflicts with structured inputs.")
    for aliases, _arity, owner, name in OPTIONS:
        if name and environment[name] is not None:
            if owner is not None and resolved.get(owner) is not None:
                raise ValueError(f"Inherited {name} duplicates structured startup authority.")
            if owner is None and aliases[0] in selected:
                raise ValueError(f"Inherited {name} duplicates extraArgs authority.")
    rawKwargs = environment["LLAMA_ARG_CHAT_TEMPLATE_KWARGS"]
    try:
        inherited = {} if rawKwargs is None else json.loads(rawKwargs)
        validateKwargs(inherited)
    except (ValueError, TypeError):
        raise ValueError("Inherited template kwargs are invalid.") from None
    configured = resolved.get("chatTemplateKwargs") or {}
    envFields = {owner: name for _aliases, _arity, owner, name in OPTIONS if owner and name}
    for key, field in _TEMPLATE_KEYS.items():
        if key in inherited and (resolved.get(field) is not None or key in configured
                                 or environment[envFields[field]] is not None):
            raise ValueError(f"Inherited template kwargs duplicate {field} authority.")
        if key in configured and environment[envFields[field]] is not None:
            raise ValueError(f"Inherited {envFields[field]} duplicates template kwargs authority.")
    if resolved.get("cpuMoe") is True and environment["LLAMA_ARG_N_CPU_MOE"] is not None:
        raise ValueError("Inherited partial expert placement conflicts with cpuMoe.")
    if resolved.get("cpuMoeLayers") is not None and environment["LLAMA_ARG_CPU_MOE"] is not None:
        raise ValueError("Inherited all-expert placement conflicts with cpuMoeLayers.")
    if environment["LLAMA_ARG_CPU_MOE"] is not None and environment["LLAMA_ARG_N_CPU_MOE"] is not None:
        raise ValueError("Inherited expert placement has duplicate authority.")
    usesTemplate = (any(resolved.get(key) is not None for key in LC01_FIELDS[2:]) or bool(inherited)
                    or any(environment[envFields[key]] is not None for key in LC01_FIELDS[2:]))
    if usesTemplate and (selected.get("--jinja") == "--no-jinja"
                         or environment["LLAMA_ARG_JINJA"] in {"0", "false", "off", "disabled"}):
        raise ValueError("Template reasoning controls conflict with disabled Jinja.")
    templateSources = sum((
        "--chat-template" in selected, "--chat-template-file" in selected,
        environment["LLAMA_ARG_CHAT_TEMPLATE"] is not None,
        environment["LLAMA_ARG_CHAT_TEMPLATE_FILE"] is not None,
    ))
    if templateSources > 1:
        raise ValueError("Only one chat template source may be configured.")


def startupArgs(startup: Mapping[str, ImmutableValue]) -> list[str]:
    """Emit only explicitly configured new controls in a deterministic order."""
    args = []
    for flag, field in (
        ("--moe-cache-mib", "moeCacheMiB"), ("--reasoning", "serverReasoning"),
        ("--reasoning-effort", "serverReasoningEffort"),
        ("--reasoning-budget", "serverReasoningBudget"),
        ("--reasoning-budget-message", "serverReasoningBudgetMessage"),
        ("--reasoning-format", "serverReasoningFormat"),
    ):
        if startup[field] is not None:
            args.extend((flag, str(startup[field])))
    if startup["cpuMoe"] is True:
        args.append("--cpu-moe")
    if startup["serverReasoningPreserve"] is not None:
        args.append("--reasoning-preserve" if startup["serverReasoningPreserve"] else "--no-reasoning-preserve")
    if startup["chatTemplateKwargs"] is not None:
        args.extend(("--chat-template-kwargs", json.dumps(jsonValue(startup["chatTemplateKwargs"]),
                     sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))))
    for aliases, arity, field, _environment in SPEC_OPTIONS:
        value = startup[field]
        if value is None:
            continue
        if arity == 0:
            args.append(aliases[0] if value else aliases[1])
        else:
            rendered = ",".join(value) if field in {"specMethods", "draftDevices"} else str(value)
            args.extend((aliases[0], rendered))
    return args


def executableIdentity(path: Path) -> dict[str, ImmutableValue]:
    """Read executable content identity without loading models or executing code."""
    try:
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        stat = path.stat()
        return {"status": "identified", "path": str(path), "sha256": digest,
                "size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
    except OSError:
        return {"status": "missing-or-unreadable", "path": str(path), "sha256": None}


def _probeCommand(path: Path, switch: str, environment: Mapping[str, str], *,
                  timeout: float = 5.0, limit: int = 262144) -> dict[str, ImmutableValue]:
    """Bound child lifetime and captured bytes; discard excess output and reap the child.

    A temporary file avoids unbounded PIPE memory or reader-thread lifetime. Size is
    polled every 10 ms; the file may overshoot the limit between polls. Only limit
    bytes are retained in evidence, and the process is killed on overflow/timeout.
    """
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen([str(path), switch], stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=output, env=environment)
        except OSError:
            return {"status": "execution-failed", "output": ""}
        deadline = time.monotonic() + timeout
        status = "completed"
        try:
            while process.poll() is None:
                if os.fstat(output.fileno()).st_size > limit:
                    status = "output-limit"
                    break
                if time.monotonic() >= deadline:
                    status = "timeout"
                    break
                time.sleep(0.01)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1.0)
        if os.fstat(output.fileno()).st_size > limit:
            status = "output-limit"
        output.seek(0)
        captured = output.read(limit).decode("utf-8", errors="replace")
        if status == "completed" and process.returncode != 0:
            status = "nonzero-exit"
        return {"status": status, "exitCode": process.returncode, "output": captured}


def probeCapabilities(path: Path, *, refresh: bool = False,
                      environment: Mapping[str, str] | None = None,
                      workingDirectory: str | None = None) -> Mapping[str, ImmutableValue]:
    """Cache bounded help/version evidence by executable content and stat identity."""
    executable = executableIdentity(path)
    if executable["status"] != "identified":
        return freeze({"status": executable["status"], "executable": executable})
    sourceEnvironment = dict(os.environ if environment is None else environment)
    directory = Path(workingDirectory or str(Path.cwd()))
    for name in CONFIG_ENV:
        if sourceEnvironment.get(name):
            sourceEnvironment[name] = str((directory / sourceEnvironment[name]).resolve())
    configInputs = {name: sourceEnvironment.get(name) for name in CONFIG_ENV}
    key = identity({"executable": executable, "configInputs": configInputs,
                    "automaticConfig": automaticConfig(configInputs)})
    if not refresh and key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    environment = {key: value for key, value in sourceEnvironment.items()
                   if not key.startswith("LLAMA_ARG_") and key != "MTMD_BACKEND_DEVICE"}
    version = _probeCommand(path, "--version", environment)
    helpResult = _probeCommand(path, "--help", environment)
    status = "advertised-pinned-surface"
    if version["status"] != "completed" or helpResult["status"] != "completed":
        status = "probe-failed"
    claims = re.findall(r"(?i)\b[0-9a-f]{9,40}\b", version["output"])
    matching = [claim for claim in claims if SOURCE_COMMIT.startswith(claim.lower())]
    fullMismatch = any(len(claim) == 40 and claim.lower() != SOURCE_COMMIT for claim in claims)
    if status != "probe-failed" and (not matching or fullMismatch):
        status = "identity-mismatch"
    advertised = tuple(sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*\b", helpResult["output"]))))
    if status == "advertised-pinned-surface" and not advertised:
        status = "malformed-help"
    result = freeze({"status": status, "executable": executable,
                     "sourceCommit": SOURCE_COMMIT, "versionProbe": version,
                     "helpProbe": helpResult, "advertisedFlags": advertised,
                     "identityClaim": "reported-prefix-match" if matching else "unverified",
                     "probeEnvironment": "LLAMA_ARG_* removed; no model arguments",
                     "effectiveBehavior": "unverified"})
    # Keep the process-wide cache bounded even if a caller checks many executables.
    if len(_PROBE_CACHE) >= 16:
        _PROBE_CACHE.pop(next(iter(_PROBE_CACHE)))
    _PROBE_CACHE[key] = result
    return result
