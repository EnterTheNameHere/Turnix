# file: tests/first_party/llmDrivers/test_llamaCppConfiguration.py ; version: 2
"""LC-01 startup authority and evidence tests without models, GPUs or inference."""
from __future__ import annotations

from tests.first_party.llmDrivers.controlledLlama import launchOwnedDriver, stopOwnedDriver, profileOwnedDriver, SharedComposition

import importlib.util
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from backend.llm.llmTypes import LlmCallRequest, LlmQuery
from backend.runtime.sharedServices import (
    SharedServiceConflictError,
    SharedServiceRegistry,
    bindApplicationRunSharedServices,
    unbindApplicationRunSharedServices,
)

_PATH = Path(__file__).parents[3] / "first-party/llmDrivers/llamaCpp/structuredCodeEntry.py"
_SPEC = importlib.util.spec_from_file_location("llamaCppConfigurationTestAdapter", _PATH)
adapter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adapter)
impl = adapter._impl
configuration = impl._configuration


@pytest.fixture(autouse=True)
def cleanEnvironment(monkeypatch):
    """Isolate pinned relevant environment and capability cache in every case."""
    for name in configuration.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    configuration._PROBE_CACHE.clear()


@pytest.fixture
def config(tmp_path):
    """Create placeholder input files; no executable fixture is ever inferred live."""
    executable = tmp_path / "private-server.exe"
    executable.write_bytes(b"not executable")
    model = tmp_path / "private-model.gguf"
    model.write_bytes(b"not a model")
    return {"manageServer": True, "executable": str(executable),
            "models": {"model-a": {"modelPath": str(model)}}}


def driver(config, **settings):
    """Apply per-model settings and construct a lazy driver with the real parser."""
    config["models"]["model-a"].update(settings)
    return impl.LlamaCppDriver(config)


class Process:
    """Represent a resident fake process and track deterministic teardown."""

    def __init__(self):
        """Begin resident without launching an operating-system process."""
        self.returncode = None
        self.stops = 0

    def poll(self):
        """Expose process residency to the ordinary lazy-start path."""
        return self.returncode

    def terminate(self):
        """Record normal last-lease teardown."""
        self.stops += 1
        self.returncode = 0

    def kill(self):
        """Model the forced teardown fallback."""
        self.returncode = -1

    def wait(self, timeout):
        """Return exit evidence without any blocking work."""
        return self.returncode


def fakeLaunch(monkeypatch):
    """Replace process and health boundaries, recording actual Popen arguments."""
    calls = []

    def popen(args, **kwargs):
        """Capture the exact argv/environment handed to process construction."""
        process = Process()
        calls.append((tuple(args), kwargs, process))
        return process

    def ready(self, timeout):
        """Represent a successful health wait without making HTTP calls."""
        return None

    monkeypatch.setattr(impl.subprocess, "Popen", popen)
    monkeypatch.setattr(impl.LlamaCppDriver, "_waitReady", ready)
    return calls


def test_all_new_controls_preserve_independence_and_exact_argv(config):
    """Off, effort, budget/message and extraction mode are independent startup defaults."""
    value = driver(config, moeCacheMiB=0, cpuMoe=False, cpuMoeLayers=0,
                   serverReasoning="off", serverReasoningEffort="template-custom",
                   serverReasoningBudget=32, serverReasoningBudgetMessage="-stop thinking",
                   serverReasoningFormat="deepseek-legacy", serverReasoningPreserve=False,
                   chatTemplateKwargs={"custom": {"finite": [1, None, False, 0.5]}})
    args = value._serverArgs(value.models["model-a"])
    assert args[args.index("--reasoning") + 1] == "off"
    assert args[args.index("--reasoning-effort") + 1] == "template-custom"
    assert args[args.index("--reasoning-budget") + 1] == "32"
    assert args[args.index("--reasoning-budget-message") + 1] == "-stop thinking"
    assert args[args.index("--reasoning-format") + 1] == "deepseek-legacy"
    assert args[args.index("--moe-cache-mib") + 1] == "0"
    assert "--cpu-moe" not in args
    assert "--no-reasoning-preserve" in args
    assert json.loads(args[args.index("--chat-template-kwargs") + 1])["custom"]["finite"] == [1, None, False, 0.5]


@pytest.mark.parametrize("settings", [
    {"moeCacheMiB": True}, {"moeCacheMiB": -1}, {"moeCacheMiB": 2**31},
    {"moeCacheMiB": 1.0}, {"cpuMoe": 1}, {"serverReasoning": "yes"},
    {"serverReasoning": False}, {"serverReasoningBudget": True},
    {"serverReasoningBudget": -2}, {"serverReasoningBudget": 2**31},
    {"serverReasoningBudget": float("inf")}, {"serverReasoningEffort": "  "},
    {"serverReasoningEffort": 1}, {"serverReasoningBudgetMessage": False},
    {"serverReasoningFormat": "legacy"}, {"serverReasoningPreserve": 1},
    {"chatTemplateKwargs": []}, {"chatTemplateKwargs": {"x": float("nan")}},
    {"chatTemplateKwargs": {1: "key"}}, {"chatTemplateKwargs": {"x": object()}},
    {"chatTemplateKwargs": {"enable_thinking": "false"}},
    {"chatTemplateKwargs": {"preserve_reasoning": 1}},
    {"chatTemplateKwargs": {"reasoning_effort": False}},
    {"cpuMoe": True, "cpuMoeLayers": 0}, {"cpuMoeLayers": 2**31},
])
def test_invalid_exact_types_and_ranges_fail_before_start(config, settings):
    """Invalid types/ranges never depend on an actual binary rejecting them."""
    with pytest.raises((TypeError, ValueError)):
        driver(config, **settings)


@pytest.mark.parametrize("field,key,value", [
    ("serverReasoning", "enable_thinking", "auto"),
    ("serverReasoningEffort", "reasoning_effort", "default"),
    ("serverReasoningPreserve", "preserve_reasoning", False),
])
def test_duplicate_dedicated_and_template_authority_rejected_even_equal(config, field, key, value):
    """Dedicated options cannot race corresponding template kwargs, even for auto/default."""
    kwarg = False if key != "reasoning_effort" else "default"
    with pytest.raises(ValueError, match="Duplicate authority"):
        driver(config, **{field: value, "chatTemplateKwargs": {key: kwarg}})


def test_inheritance_null_omission_and_caller_mutation(config):
    """Nested values freeze once and null clears an inherited option/object as a whole."""
    config.update(serverReasoning="off", moeCacheMiB=17, chatTemplateKwargs={"old": True})
    raw = config["models"]["model-a"]
    raw.update(moeCacheMiB=None, chatTemplateKwargs={"new": ["frozen"]})
    value = impl.LlamaCppDriver(config)
    before = value.configurationEvidence()
    raw["chatTemplateKwargs"]["new"].append("mutated")
    config["serverReasoning"] = "on"
    after = value.configurationEvidence()
    assert before == after
    request = before["requested"]
    assert request["moeCacheMiB"] == {"value": None, "source": "model", "specified": True}
    assert request["serverReasoning"]["source"] == "driver"
    assert request["chatTemplateKwargs"]["value"] == {"new": ("frozen",)}
    assert request["serverReasoningBudget"]["specified"] is False
    with pytest.raises(TypeError):
        request["chatTemplateKwargs"]["value"]["new"] = ()
    assert "--moe-cache-mib" not in value._serverArgs(value.models["model-a"])


def test_cycles_and_private_value_diagnostics(config):
    """Recursive/non-JSON configuration fails without reflecting arbitrary values."""
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="finite JSON"):
        driver(config, chatTemplateKwargs={"cycle": cycle})
    config["models"]["model-a"]["chatTemplateKwargs"] = {"PRIVATE_SENTINEL": object()}
    with pytest.raises(ValueError) as caught:
        impl.LlamaCppDriver(config)
    assert "PRIVATE_SENTINEL" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("aliases,arity,owner,env", [item for item in configuration.OPTIONS if item[2]])
def test_every_reserved_alias_equals_and_separate_forms(config, aliases, arity, owner, env):
    """All pinned owned aliases reserve their setting, including paired switches."""
    for alias in aliases:
        for args in ([alias, "value"], [alias + "=value"]):
            with pytest.raises(ValueError, match="first-class flag"):
                driver(config, extraArgs=args)


def test_option_values_and_unknown_options_remain_opaque(config):
    """Known option values can resemble flags; unknown options cannot conceal owned flags."""
    value = driver(config, extraArgs=["--chat-template", "--reasoning", "--experimental=-text"])
    assert value.models["model-a"].extraArgs[1] == "--reasoning"
    assert value.configurationEvidence()["extraArgsCertification"] == "uncertified"
    for args in (["--unknown", "--reasoning"], ["--chat-template"],
                 ["--jinja", "--no-jinja"], ["--chat-template=x", "--chat-template-file=y"],
                 ["--jinja=on"]):
        with pytest.raises(ValueError):
            driver(config, extraArgs=args)
    for option in configuration._REMOVED:
        with pytest.raises(ValueError, match="removed"):
            driver(config, extraArgs=[option + "=3"])


@pytest.mark.parametrize("kwargs", [
    {"serverReasoning": "off"}, {"serverReasoningFormat": "none"},
    {"chatTemplateKwargs": {}},
])
def test_explicit_no_jinja_is_conflicting(config, kwargs):
    """Explicit template controls must not silently bypass their template engine."""
    with pytest.raises(ValueError, match="Jinja"):
        driver(config, **kwargs, extraArgs=["--no-jinja"])


@pytest.mark.parametrize("settings,env", [
    ({"moeCacheMiB": 0}, {"LLAMA_ARG_MOE_CACHE_MIB": "0"}),
    ({"cpuMoe": False}, {"LLAMA_ARG_CPU_MOE": "false"}),
    ({"serverReasoning": "off"}, {"LLAMA_ARG_CHAT_TEMPLATE_KWARGS": '{"enable_thinking":false}'}),
    ({"chatTemplateKwargs": {"enable_thinking": False}}, {"LLAMA_ARG_REASONING": "off"}),
    ({}, {"LLAMA_ARG_CHAT_TEMPLATE_KWARGS": '{"reasoning_effort":"high"}', "LLAMA_ARG_REASONING_EFFORT": "high"}),
    ({"cpuMoe": True}, {"LLAMA_ARG_N_CPU_MOE": "0"}),
    ({"cpuMoeLayers": 0}, {"LLAMA_ARG_CPU_MOE": "true"}),
    ({}, {"LLAMA_ARG_CPU_MOE": "true", "LLAMA_ARG_N_CPU_MOE": "1"}),
    ({"extraArgs": ["--chat-template", "x"]}, {"LLAMA_ARG_CHAT_TEMPLATE_FILE": "path"}),
    ({"serverReasoning": "off"}, {"LLAMA_ARG_JINJA": "false"}),
    ({}, {"LLAMA_ARG_MODEL_URL": "https://private.invalid/model"}),
])
def test_inherited_collisions_include_template_and_placement_aliases(config, monkeypatch, settings, env):
    """Inherited aliases are checked as authorities, without exposing private values."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        driver(config, **settings)


def test_environment_frozen_at_acquisition_and_nonowned_inherited_at_launch(config, monkeypatch):
    """Later ambient changes cannot alter owned startup inputs on an existing lease."""
    calls = fakeLaunch(monkeypatch)
    monkeypatch.setenv("LLAMA_ARG_REASONING", "off")
    value = driver(config)
    monkeypatch.setenv("LLAMA_ARG_REASONING", "on")
    monkeypatch.setenv("LLAMA_ARG_THINK_BUDGET", "999")
    monkeypatch.setenv("LC01_NONOWNED", "fresh")
    launchOwnedDriver(value)
    assert len(calls) == 1
    environment = calls[0][1]["env"]
    assert environment["LLAMA_ARG_REASONING"] == "off"
    assert "LLAMA_ARG_THINK_BUDGET" not in environment
    assert environment["LC01_NONOWNED"] == "fresh"
    evidence = value.configurationEvidence()
    assert evidence["launch"]["environment"]["LLAMA_ARG_REASONING"] == "off"
    assert evidence["launch"]["actualArgv"] == calls[0][0]
    assert evidence["launch"]["plannedArgv"] == calls[0][0]
    assert evidence["build"]["status"] == "unverified"
    assert evidence["build"]["executable"]["sha256"] is not None
    assert evidence["inputs"][0]["contentSha256"] is None
    launchOwnedDriver(value)
    assert len(calls) == 1
    stopOwnedDriver(value)
    assert calls[0][2].stops == 1
    assert evidence["launch"]["status"] == "ready"
    assert value.configurationEvidence()["launch"]["status"] == "stopped"


def test_shared_lease_environment_identity_and_no_leaks(config, monkeypatch):
    """Different frozen catalogs share a stable domain, without implicit replacement."""
    calls = fakeLaunch(monkeypatch)
    shared = SharedComposition()
    first = shared.bind(config)
    monkeypatch.setenv("LLAMA_ARG_REASONING", "off")
    second = shared.bind(config)
    try:
        assert first.binding.domain is second.binding.domain and not calls
        assert first.binding.profiles[None] is not second.binding.profiles[None]
        monkeypatch.setenv("LLAMA_ARG_REASONING", "on")
        first.transition()
        assert "LLAMA_ARG_REASONING" not in calls[0][1]["env"]
        first.close()
        assert calls[0][2].stops == 0
        second.close()
        assert calls[0][2].stops == 1 and len(calls) == 1
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("module", [adapter, impl])
def test_entire_automatic_metadata_is_sanitized_private_evidence_lossless(config, monkeypatch, module):
    """Both adapters project safe metadata and preserve private evidence separately."""
    fakeLaunch(monkeypatch)
    sentinel = "PRIVATE_SENTINEL"
    value = driver(config, serverReasoningEffort=sentinel,
                   serverReasoningBudgetMessage=sentinel, cacheTypeK=sentinel,
                   chatTemplateKwargs={"nested": [sentinel]}, extraArgs=["--secret=" + sentinel])
    profile = profileOwnedDriver(module.LlamaCppStreamProvider(driver=value), model=None, providerOptions={})
    encoded = json.dumps(configuration.jsonValue(profile.metadata))
    assert sentinel not in encoded
    assert "private-server" not in encoded and "private-model" not in encoded
    assert "baseUrl" not in profile.metadata and "activeModel" not in profile.metadata
    assert "extraArgs" not in profile.metadata["launchProfile"]
    private = value.configurationEvidence()
    assert private["requested"]["serverReasoningBudgetMessage"]["value"] == sentinel
    assert private["requested"]["chatTemplateKwargs"]["value"]["nested"] == (sentinel,)
    assert "--secret=" + sentinel in private["launch"]["actualArgv"]
    assert private["providerReportedEffective"]["status"] == "unknown-unqueried"
    assert private["profileId"] == profile.metadata["configurationEvidence"]["profileId"]


@pytest.mark.parametrize("module", [adapter, impl])
def test_automatic_connection_diagnostics_do_not_reflect_private_endpoint(monkeypatch, module):
    """Private endpoint and underlying exception text stay out of rendered diagnostics."""
    value = impl.LlamaCppDriver({"baseUrl": "http://PRIVATE_SENTINEL.invalid"})

    def fail(*args, **kwargs):
        """Simulate a lower-level error containing a credential/private URL."""
        raise URLError("PRIVATE_SENTINEL")

    monkeypatch.setattr(module.urlRequest, "urlopen", fail)
    request = LlmCallRequest(query=LlmQuery(formatId="text/plain", payload="not retained"))
    with pytest.raises(impl.LlmProviderConnectionError) as caught:
        list(module.LlamaCppStreamProvider(driver=value).stream(request))
    assert "PRIVATE_SENTINEL" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("field,value", [
    ("moeCacheMiB", 0), ("cpuMoe", False), ("serverReasoning", "off"),
    ("serverReasoningEffort", "default"), ("serverReasoningBudget", -1),
    ("serverReasoningBudgetMessage", ""), ("serverReasoningFormat", "none"),
    ("serverReasoningPreserve", False), ("chatTemplateKwargs", {}),
])
def test_external_rejects_new_nonnull_startup_controls_at_every_scope(field, value):
    """External startup configuration is rejected even for zero, false or empty object."""
    for config in ({field: value}, {"models": {"a": {"modelPath": "x", field: value}}}):
        with pytest.raises(ValueError, match="external server separately"):
            impl.LlamaCppDriver(config)


def test_external_legacy_is_unapplied_and_minimal_defaults_preserved(config):
    """Old external settings remain readable, while evidence makes ownership explicit."""
    value = impl.LlamaCppDriver({"models": {"a": {"modelPath": "x", "cpuMoeLayers": 2}}})
    evidence = value.configurationEvidence()
    assert evidence["launch"]["status"] == "external-unapplied"
    assert evidence["launch"]["actualArgv"] is None and evidence["launch"]["plannedArgv"] is None
    assert evidence["build"]["status"] == "external-unverified"
    with pytest.raises(ValueError, match="external"):
        value.checkCapabilities()
    with pytest.raises(ValueError, match="External"):
        impl.LlamaCppDriver({"capabilityCheck": "required"})
    minimal = driver(config)
    assert not any(flag.startswith("--reasoning") for flag in minimal._serverArgs(minimal.models["model-a"]))
    assert minimal.configurationEvidence()["launch"]["actualArgv"] is None


def probeResponses(monkeypatch, *, version="version 0.8.0-dev (060b2fea4)", helpText=None, status="completed"):
    """Provide deterministic bounded-probe responses, tracking explicit calls."""
    calls = []
    if helpText is None:
        helpText = " ".join(flag for item in configuration.OPTIONS for flag in item[0] if flag.startswith("--"))

    def command(path, switch, environment):
        """Record probe arguments/environment without executing the placeholder file."""
        calls.append((path, switch, environment))
        return {"status": status, "exitCode": 0, "output": version if switch == "--version" else helpText}

    monkeypatch.setattr(configuration, "_probeCommand", command)
    return calls


def test_probe_explicit_lazy_cached_and_build_replacement(config, monkeypatch):
    """Advertised source/flags are distinct from effective behavior and cache invalidates content changes."""
    calls = probeResponses(monkeypatch)
    value = driver(config, serverReasoning="off", moeCacheMiB=0)
    assert not calls
    before = value.configurationEvidence()
    result = value.checkCapabilities()
    assert result["status"] == "advertised-pinned-surface"
    assert result["effectiveBehavior"] == "unverified"
    assert result["identityClaim"] == "reported-prefix-match"
    assert before["build"]["status"] == "unverified"
    assert before["profileId"] == value.configurationEvidence()["profileId"]
    assert len(calls) == 2
    value.checkCapabilities()
    assert len(calls) == 2
    value.checkCapabilities(refresh=True)
    assert len(calls) == 4
    Path(config["executable"]).write_bytes(b"new executable")
    value.checkCapabilities()
    assert len(calls) == 6
    assert value.process is None
    assert all(not key.startswith("LLAMA_ARG_") for key in calls[0][2])


@pytest.mark.parametrize("options,expected", [
    ({"version": "0.8.0-dev build 12031"}, "identity-mismatch"),
    ({"version": "060b2fea4 " + "f" * 40}, "identity-mismatch"),
    ({"version": "123456789"}, "identity-mismatch"),
    ({"helpText": "--model --host --port --parallel"}, "missing-flags"),
    ({"helpText": "not help"}, "malformed-help"),
    ({"status": "timeout"}, "probe-failed"),
    ({"status": "output-limit"}, "probe-failed"),
    ({"status": "nonzero-exit"}, "probe-failed"),
    ({"status": "execution-failed"}, "probe-failed"),
])
def test_required_probe_rejects_missing_or_different_surface_before_launch(config, monkeypatch, options, expected):
    """Required checks fail without attempting model startup or inventing support."""
    calls = fakeLaunch(monkeypatch)
    probeResponses(monkeypatch, **options)
    config["capabilityCheck"] = "required"
    value = driver(config, serverReasoning="off")
    with pytest.raises(ValueError, match="capability check"):
        launchOwnedDriver(value)
    assert not calls
    assert value.configurationEvidence()["build"]["status"] == expected


def test_missing_executable_probe_and_required_success(config, monkeypatch):
    """Missing/replaced executable is not supported; required success still means advertisement."""
    assert configuration.probeCapabilities(Path(config["executable"] + ".missing"))["status"] == "missing-or-unreadable"
    calls = fakeLaunch(monkeypatch)
    probeResponses(monkeypatch)
    config["capabilityCheck"] = "required"
    value = driver(config, serverReasoning="off")
    launchOwnedDriver(value)
    assert len(calls) == 1
    assert value.configurationSummary()["buildStatus"] == "advertised-pinned-surface"
    stopOwnedDriver(value)


def test_failed_launch_and_health_have_honest_argv_and_safe_diagnostics(config, monkeypatch):
    """Only successful process creation records actual argv; readiness is a separate fact."""
    def fail(*args, **kwargs):
        """Represent a failed OS launch containing private executable details."""
        raise OSError("PRIVATE_SENTINEL")

    value = driver(config, serverReasoningBudgetMessage="PRIVATE_SENTINEL")
    monkeypatch.setattr(impl.subprocess, "Popen", fail)
    with pytest.raises(impl.LlmProviderConnectionError) as caught:
        launchOwnedDriver(value)
    assert "PRIVATE_SENTINEL" not in "".join(traceback.format_exception(caught.value))
    assert value.configurationEvidence()["launch"]["actualArgv"] is None
    assert value.configurationEvidence()["launch"]["status"] == "launch-failed"
    calls = fakeLaunch(monkeypatch)
    monkeypatch.setattr(impl.LlamaCppDriver, "_waitReady", fail)
    with pytest.raises(impl.LlmProviderConnectionError):
        launchOwnedDriver(value)
    evidence = value.configurationEvidence()
    assert evidence["launch"]["actualArgv"] == calls[0][0]
    assert evidence["launch"]["status"] == "startup-failed"
    assert calls[0][2].stops == 1 and value.process is None


def test_real_bounded_probe_process_handles_timeout_and_excess_output(tmp_path):
    """Exercise the actual process bound/reaping using Python scripts, never llama/model inference."""
    script = tmp_path / "probe-fixture.py"
    script.write_text('# file: probe-fixture.py ; version: 1\nimport time\ntime.sleep(10)\n', encoding="utf-8")
    result = configuration._probeCommand(Path(sys.executable), str(script), dict(configuration.os.environ), timeout=0.2)
    assert result["status"] == "timeout"
    script.write_text('# file: probe-fixture.py ; version: 1\nprint("x" * 100000)\n', encoding="utf-8")
    result = configuration._probeCommand(Path(sys.executable), str(script), dict(configuration.os.environ), limit=1024)
    assert result["status"] == "output-limit"
    assert len(result["output"].encode()) <= 1024


@pytest.mark.parametrize("negative,field,value", [
    ("LLAMA_ARG_NO_KV_UNIFIED", "unifiedKv", False),
    ("LLAMA_ARG_NO_KV_OFFLOAD", "kvOffload", False),
    ("LLAMA_ARG_NO_REASONING_PRESERVE", "serverReasoningPreserve", False),
])
def test_pinned_legacy_negative_environment_aliases_are_owned(config, monkeypatch, negative, field, value):
    """Legacy LLAMA_ARG_NO_* presence participates in authority and frozen identity."""
    monkeypatch.setenv(negative, "ignored-text")
    with pytest.raises(ValueError, match="duplicates"):
        driver(config, **{field: value})
    config["models"]["model-a"].pop(field)
    inherited = driver(config)
    assert inherited._prepared.environment[negative] == "ignored-text"
    before = inherited._prepared.compatibilityKey()
    monkeypatch.delenv(negative)
    assert driver(config)._prepared.compatibilityKey() != before


def test_negative_jinja_alias_and_both_environment_polarities(config, monkeypatch):
    """Negative aliases force false regardless of text, and dual polarities are ambiguous."""
    monkeypatch.setenv("LLAMA_ARG_NO_JINJA", "false")
    with pytest.raises(ValueError, match="Jinja"):
        driver(config, serverReasoning="off")
    config["models"]["model-a"].clear()
    config["models"]["model-a"]["modelPath"] = str(Path(config["executable"]))
    monkeypatch.setenv("LLAMA_ARG_JINJA", "true")
    with pytest.raises(ValueError, match="positive and negative"):
        driver(config)


def test_template_file_and_auxiliary_inputs_are_frozen_as_paths_not_content_hashes(config, monkeypatch, tmp_path):
    """Input resolution stays stable after cwd changes and does not invent file hashes."""
    projector = tmp_path / "projector.gguf"
    projector.write_bytes(b"fake")
    value = driver(config, mmprojPath=str(projector), extraArgs=["--chat-template-file", "relative.jinja"])
    before = value.configurationEvidence()
    monkeypatch.chdir(tmp_path)
    after = value.configurationEvidence()
    assert before == after
    assert [item["role"] for item in before["inputs"]] == ["target", "mmproj", "template"]
    assert all(item["contentSha256"] is None for item in before["inputs"])
    assert before["inputs"][-1]["configuredPath"] == "relative.jinja"
    calls = fakeLaunch(monkeypatch)
    launchOwnedDriver(value)
    assert calls[0][1]["cwd"] == str(Path(before["inputs"][-1]["resolvedPath"]).parent)
    stopOwnedDriver(value)


def test_profile_identity_distinguishes_explicit_defaults_and_ordered_experimental_arguments(config):
    """Configuration identity preserves values and argv ordering, not inferred equivalence."""
    omitted = driver(config).configurationEvidence()["profileId"]
    zero = driver(config, moeCacheMiB=0).configurationEvidence()["profileId"]
    assert omitted != zero
    first = driver(config, extraArgs=["--a=1", "--b=2"]).configurationEvidence()["profileId"]
    second = driver(config, extraArgs=["--b=2", "--a=1"]).configurationEvidence()["profileId"]
    assert first != second


def test_old_launch_build_is_retained_after_probe_of_replaced_executable(config, monkeypatch):
    """Current file probe never rewrites the build evidence captured for an earlier launch."""
    fakeLaunch(monkeypatch)
    probeResponses(monkeypatch)
    value = driver(config)
    launchOwnedDriver(value)
    old = value.configurationEvidence()
    Path(config["executable"]).write_bytes(b"replacement")
    value.checkCapabilities()
    new = value.configurationEvidence()
    assert old["launch"]["build"] == new["launch"]["build"]
    assert old["launch"]["build"]["executable"]["sha256"] != new["build"]["executable"]["sha256"]
    assert old["profileId"] == new["profileId"]
    stopOwnedDriver(value)


def test_direct_probe_after_executable_disappears_and_successful_probe_with_unknown_args(config, monkeypatch):
    """Identity disappearance stays explicit and successful help does not certify escape hatches."""
    probeResponses(monkeypatch)
    value = driver(config, extraArgs=["--unverified-experiment"])
    assert value.checkCapabilities()["status"] == "advertised-pinned-surface"
    assert value.configurationSummary()["extraArgsCertification"] == "uncertified"
    Path(config["executable"]).unlink()
    assert value.checkCapabilities()["status"] == "missing-or-unreadable"


def test_zero_budget_unrestricted_auto_defaults_and_true_switches(config):
    """Explicit zero/unrestricted/default/auto and true switches remain distinguishable."""
    value = driver(config, serverReasoning="auto", serverReasoningEffort="default",
                   serverReasoningBudget=0, serverReasoningBudgetMessage="", cpuMoe=True,
                   serverReasoningFormat="auto", serverReasoningPreserve=True, unifiedKv=False)
    args = value._serverArgs(value.models["model-a"])
    assert "--cpu-moe" in args and "--reasoning-preserve" in args and "--no-kv-unified" in args
    assert args[args.index("--reasoning-budget") + 1] == "0"
    assert args[args.index("--reasoning-budget-message") + 1] == ""
    assert args[args.index("--reasoning-effort") + 1] == "default"
    assert args[args.index("--reasoning-format") + 1] == "auto"
    unlimited = driver(config, serverReasoningBudget=-1)
    assert unlimited.configurationEvidence()["requested"]["serverReasoningBudget"]["value"] == -1


def test_required_probe_checks_inherited_controls_as_well_as_emitted_flags(config, monkeypatch):
    """Inherited startup authorities require their pinned advertised flag surface too."""
    monkeypatch.setenv("LLAMA_ARG_REASONING", "off")
    monkeypatch.setenv("LLAMA_ARG_NO_REASONING_PRESERVE", "any")
    probeResponses(monkeypatch, helpText="--model --host --port --parallel")
    value = driver(config)
    result = value.checkCapabilities()
    assert result["status"] == "missing-flags"
    assert "--reasoning" in result["missingFlags"]
    assert "--no-reasoning-preserve" in result["missingFlags"]
