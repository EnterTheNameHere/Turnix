# file: first-party/llmDrivers/llamaCpp/configuration.py ; version: 1
"""Provider-local startup authority, immutable inputs and bounded build evidence."""
from __future__ import annotations

import hashlib
import json
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
NEW_FIELDS = (
    "moeCacheMiB", "cpuMoe", "serverReasoning", "serverReasoningEffort",
    "serverReasoningBudget", "serverReasoningBudgetMessage", "serverReasoningFormat",
    "serverReasoningPreserve", "chatTemplateKwargs",
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
)
_BY_FLAG = {flag: item for item in OPTIONS for flag in item[0]}
_ALTERNATE_INPUTS = {
    "-mu", "--model-url", "-hf", "-hfr", "--hf-repo", "-hff", "--hf-file",
    "-dr", "--docker-repo", "-mmu", "--mmproj-url", "--mmproj-auto",
    "--no-mmproj", "--no-mmproj-auto", "--models-dir", "--models-preset",
}
_ALTERNATE_ENV = (
    "LLAMA_ARG_MODEL_URL", "LLAMA_ARG_HF_REPO", "LLAMA_ARG_HF_FILE",
    "LLAMA_ARG_DOCKER_REPO", "LLAMA_ARG_MMPROJ_URL", "LLAMA_ARG_MMPROJ_AUTO",
    "LLAMA_ARG_MODELS_DIR", "LLAMA_ARG_MODELS_PRESET",
)
# common_arg::get_value_from_env/get_env also expose these legacy negative aliases.
# Their presence forces false regardless of their textual value.
NEGATIVE_ENV = {name: name.replace("LLAMA_ARG_", "LLAMA_ARG_NO_", 1) for name in (
    "LLAMA_ARG_KV_UNIFIED", "LLAMA_ARG_KV_OFFLOAD", "LLAMA_ARG_REASONING_PRESERVE",
    "LLAMA_ARG_JINJA", "LLAMA_ARG_MMPROJ_AUTO",
)}
ENV_NAMES = tuple(sorted({item[3] for item in OPTIONS if item[3]}
                         | set(_ALTERNATE_ENV) | set(NEGATIVE_ENV.values())))
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
        self.environment = freeze({name: os.environ.get(name) for name in ENV_NAMES})
        self.workingDirectory = str(Path.cwd())

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
                         "workingDirectory": self.workingDirectory})

    def resolvePath(self, path: str) -> Path:
        """Resolve configured input paths against the captured launch directory."""
        return (Path(self.workingDirectory) / path).resolve()

    def launchEnvironment(self) -> dict[str, str]:
        """Overlay frozen owned values/absences on current non-owned inheritance."""
        environment = dict(os.environ)
        for name, value in self.environment.items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        return environment


def prepare(config: Mapping[str, object]) -> PreparedConfiguration:
    """Reuse a lease-bound snapshot or capture one for direct driver construction."""
    return config if isinstance(config, PreparedConfiguration) else PreparedConfiguration(config)


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
    return freeze(result)


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
    usesTemplate = (any(resolved.get(key) is not None for key in NEW_FIELDS[2:]) or bool(inherited)
                    or any(environment[envFields[key]] is not None for key in NEW_FIELDS[2:]))
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


def probeCapabilities(path: Path, *, refresh: bool = False) -> Mapping[str, ImmutableValue]:
    """Cache bounded help/version evidence by executable content and stat identity."""
    executable = executableIdentity(path)
    if executable["status"] != "identified":
        return freeze({"status": executable["status"], "executable": executable})
    key = identity(executable)
    if not refresh and key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    environment = {key: value for key, value in os.environ.items()
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
