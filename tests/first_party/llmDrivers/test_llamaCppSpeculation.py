# file: tests/first_party/llmDrivers/test_llamaCppSpeculation.py ; version: 2
"""LC-03 explicit method/auxiliary baseline, frozen lease inputs and combined gate."""
from __future__ import annotations

from tests.first_party.llmDrivers.controlledLlama import launchOwnedDriver, stopOwnedDriver, profileOwnedDriver, SharedComposition

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.llm.llmTypes import LlmQuery
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.registration import RegistrationScope
from backend.runtime.sharedServices import (
    SharedServiceConflictError, SharedServiceRegistry,
    bindApplicationRunSharedServices, unbindApplicationRunSharedServices,
)

_PATH = Path(__file__).parents[3] / "first-party/llmDrivers/llamaCpp/structuredCodeEntry.py"
_SPEC = importlib.util.spec_from_file_location("llamaCppSpeculationTestAdapter", _PATH)
adapter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adapter)
impl = adapter._impl
configuration = impl._configuration


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    """Isolate known/unknown speculation and automatic config discovery from host inputs."""
    for name in set(configuration.ENV_NAMES) | {key for key in configuration.os.environ if configuration.isSpeculationEnv(key)}:
        monkeypatch.delenv(name, raising=False)
    for name in configuration.CONFIG_ENV:
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    configuration._PROBE_CACHE.clear()


@pytest.fixture
def config(tmp_path):
    """Create placeholder target/executable/auxiliary files without any model processing."""
    for filename in ("server.exe", "target.gguf", "PRIVATE_DRAFT_SENTINEL.gguf"):
        (tmp_path / filename).write_bytes(b"placeholder")
    return {"manageServer": True, "executable": str(tmp_path / "server.exe"),
            "models": {"target": {"modelPath": str(tmp_path / "target.gguf")}}}


def auxiliary(config):
    """Identify the fixture auxiliary file without loading it or hashing model contents."""
    return str(Path(config["executable"]).with_name("PRIVATE_DRAFT_SENTINEL.gguf"))


def driver(config, **settings):
    """Use real model parsing while preserving exact driver/model precedence."""
    config["models"]["target"].update(settings)
    return impl.LlamaCppDriver(config)


class Process:
    """Minimal resident process used to exercise actual lazy launch and last-lease stop."""

    def __init__(self):
        """Begin alive with observable teardown count."""
        self.returncode = None
        self.stops = 0

    def poll(self):
        """Expose residency without creating an operating-system child."""
        return self.returncode

    def terminate(self):
        """Record normal final-lease process disposal."""
        self.stops += 1
        self.returncode = 0

    def kill(self):
        """Support the existing driver's emergency cleanup mechanism."""
        self.returncode = -1

    def wait(self, timeout):
        """Return deterministic exit status without blocking."""
        return self.returncode


def launch(monkeypatch):
    """Capture exact Popen arguments while preserving the driver's startup/evidence path."""
    calls = []

    def popen(args, **kwargs):
        """Record ordered argv, frozen environment and launch directory."""
        process = Process()
        calls.append((tuple(args), kwargs, process))
        return process

    def ready(self, timeout):
        """Avoid HTTP readiness calls for a deliberately fake process."""
        return None

    monkeypatch.setattr(impl.subprocess, "Popen", popen)
    monkeypatch.setattr(impl.LlamaCppDriver, "_waitReady", ready)
    return calls


def test_explicit_none_differs_from_omission_and_excludes_auxiliary_inputs(config):
    """A disabled request is explicit evidence, while omission emits no spec switch."""
    omitted = driver(config)
    assert omitted.configurationEvidence()["speculation"]["status"] == "omitted-unverified"
    assert "--spec-type" not in omitted._serverArgs(omitted.models["target"])
    disabled = driver(config, specMethods=["none"])
    evidence = disabled.configurationEvidence()
    assert evidence["speculation"]["status"] == "explicit-none-requested"
    assert evidence["profileId"] != omitted.configurationEvidence()["profileId"]
    assert evidence["requested"]["specMethods"]["value"] == ("none",)
    assert [item["role"] for item in evidence["inputs"]] == ["target"]
    assert evidence["launch"]["plannedArgv"][-2:] == ("--spec-type", "none")
    assert evidence["providerReportedEffective"]["status"] == "unknown-unqueried"


@pytest.mark.parametrize("method", ["draft-simple", "draft-eagle3", "draft-mtp", "draft-dflash", "draft-dspark",
                                    "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod", "ngram-cache"])
def test_every_pinned_method_requires_explicit_selection_and_records_requested_state(config, method):
    """Method availability/model/backend honor stays separate from validated input names."""
    settings = {"specMethods": [method]}
    if method in {"draft-simple", "draft-eagle3", "draft-dflash", "draft-dspark"}:
        settings["draftModelPath"] = auxiliary(config)
    value = driver(config, **settings)
    evidence = value.configurationEvidence()
    assert evidence["speculation"]["methods"] == (method,)
    assert evidence["speculation"]["effectiveHonor"] == "unverified"
    if "draftModelPath" in settings:
        assert evidence["inputs"][-1]["role"] == "draft"
        assert evidence["inputs"][-1]["contentSha256"] is None


@pytest.mark.parametrize("settings", [
    {"specMethods": []}, {"specMethods": "none"}, {"specMethods": [True]},
    {"specMethods": ["unknown"]}, {"specMethods": ["none", "draft-mtp"]},
    {"specMethods": ["ngram-simple", "none"]}, {"specMethods": ["draft-mtp", "draft-mtp"]},
    {"specMethods": ["draft-simple", "ngram-simple"]},
    {"draftMaxTokens": True}, {"draftMaxTokens": -1}, {"draftMaxTokens": 2**31},
    {"draftMinTokens": -1}, {"draftMinTokens": 1.0}, {"draftUbatchSize": -1},
    {"draftModelPath": " "}, {"draftDevices": "GPU0"}, {"draftDevices": []},
    {"draftDevices": ["none", "GPU0"]}, {"draftDevices": ["GPU0", "GPU0"]},
    {"draftDevices": ["GPU0,GPU1"]}, {"draftDevices": [1]},
    {"draftGpuLayers": True}, {"draftGpuLayers": "3"}, {"draftGpuLayers": 2**31},
    {"draftSampling": "auto"}, {"draftBackendSampling": 1},
    {"draftPMin": True}, {"draftPMin": -0.1}, {"draftPSplit": 1.1},
    {"draftPMin": float("nan")}, {"draftPSplit": float("inf")}, {"draftPMin": 10**1000},
])
def test_exact_types_ranges_methods_and_order_are_validated(config, settings):
    """Reject ambiguity and unsupported numeric values before any model or process access."""
    with pytest.raises(ValueError):
        driver(config, **settings)


@pytest.mark.parametrize("field,value", [
    ("draftMaxTokens", 0), ("draftMinTokens", 0), ("draftUbatchSize", 0),
    ("draftDevices", ["none"]), ("draftGpuLayers", "auto"), ("draftSampling", "greedy"),
    ("draftBackendSampling", False), ("draftPMin", 0), ("draftPSplit", 0),
])
def test_none_rejects_even_zero_false_default_or_placement_inputs(config, field, value):
    """Disabled configuration has no dormant draft authority that could invalidate its baseline."""
    with pytest.raises(ValueError, match="none conflicts"):
        driver(config, specMethods=["none"], **{field: value})


def test_auxiliary_never_implies_method_and_minimum_maximum_relationships(config):
    """Local metadata is never inspected to enable drafting implicitly."""
    for methods in (None, ["none"], ["ngram-simple"]):
        with pytest.raises(ValueError):
            driver(config, specMethods=methods, draftModelPath=auxiliary(config))
    for method in ("draft-simple", "draft-eagle3", "draft-dflash", "draft-dspark"):
        config["models"]["target"].pop("draftModelPath", None)
        with pytest.raises(ValueError, match="local auxiliary"):
            driver(config, specMethods=[method])
    with pytest.raises(ValueError, match="exceeds"):
        driver(config, specMethods=["draft-mtp"], draftMinTokens=4)
    value = driver(config, draftMaxTokens=4)
    assert value.models["target"].startup["draftMaxTokens"] == 4
    with pytest.raises(ValueError, match="exceeds"):
        driver(config, draftMaxTokens=3)


@pytest.mark.parametrize("layers", [0, -1, -2, -3, -2147483648, 2147483647, "auto", "all"])
def test_signed_gpu_layer_parser_and_special_values_are_preserved(config, layers):
    """Do not incorrectly restrict the pinned signed layer parser to nonnegative integers."""
    value = driver(config, specMethods=["draft-mtp"], draftGpuLayers=layers)
    args = value._serverArgs(value.models["target"])
    assert args[args.index("--spec-draft-ngl") + 1] == str(layers)


def test_enabled_multi_method_argv_bounds_devices_sampling_and_identity(config):
    """Preserve supported priority order, explicit extrema and ordered auxiliary placement."""
    value = driver(config, specMethods=["ngram-map-k", "draft-simple"], draftModelPath=auxiliary(config),
                   draftMaxTokens=8, draftMinTokens=2, draftUbatchSize=0, draftDevices=["GPU1", "GPU0"],
                   draftGpuLayers="all", draftSampling="probabilistic", draftBackendSampling=False,
                   draftPMin=0, draftPSplit=1)
    args = value._serverArgs(value.models["target"])
    for flag, expected in {"--spec-type": "ngram-map-k,draft-simple", "--spec-draft-n-max": "8",
                           "--spec-draft-n-min": "2", "--spec-draft-n-ubatch": "0",
                           "--spec-draft-device": "GPU1,GPU0", "--spec-draft-ngl": "all",
                           "--spec-draft-sampling": "probabilistic", "--spec-draft-p-min": "0",
                           "--spec-draft-p-split": "1"}.items():
        assert args[args.index(flag) + 1] == expected
    assert "--no-spec-draft-backend-sampling" in args
    before = value.configurationEvidence()
    config["models"]["target"]["draftDevices"].reverse()
    config["models"]["target"]["specMethods"].append("draft-mtp")
    assert value.configurationEvidence() == before
    assert before["speculation"]["upstreamPriority"] == ("ngram-map-k", "draft-simple")
    assert before["requested"]["draftModelPath"]["value"] == auxiliary(config)


def test_null_suppresses_top_level_speculation_and_all_fields_reject_in_external_mode(config):
    """New fields retain the same inheritance/null/external boundary as LC-01."""
    config.update(specMethods=["none"], draftModelPath=auxiliary(config))
    value = driver(config, draftModelPath=None)
    assert value.configurationEvidence()["requested"]["draftModelPath"]["value"] is None
    assert value.configurationEvidence()["speculation"]["status"] == "explicit-none-requested"
    for field, sample in (("specMethods", ["none"]), ("draftMaxTokens", 0), ("draftBackendSampling", False)):
        with pytest.raises(ValueError, match="external server separately"):
            impl.LlamaCppDriver({field: sample})


@pytest.mark.parametrize("args", [
    ["--spec-type", "none"], ["--spec_type=none"], ["-md", "draft.gguf"], ["--model-draft=x"],
    ["--spec_draft_model=x"], ["--spec-draft-n-min=0"], ["--draft-p-min=0"],
    ["--draft-p-split", "0.1"], ["-ngld", "all"], ["--gpu-layers-draft=0"],
    ["--n-gpu-layers-draft", "auto"], ["-devd", "none"], ["--device-draft=none"],
    ["--no-spec-draft-backend-sampling"], ["--no_spec_draft_backend_sampling"],
])
def test_speculative_alias_and_equals_ownership_cannot_bypass_structure(config, args):
    """Include pinned long underscore normalization while respecting value arities."""
    with pytest.raises(ValueError, match="first-class"):
        driver(config, extraArgs=args)


@pytest.mark.parametrize("args", [
    ["--spec-draft-hf", "private/repo"], ["-hfd", "private/repo"], ["-hfrd", "private/repo"],
    ["--hf-repo-draft=x"], ["--spec_draft_hf=x"], ["--spec-draft-url=x"],
    ["--spec-default"], ["--mtp"], ["--eagle3"], ["--dflash"],
    ["--fim-qwen-7b-spec"], ["--fim-qwen-14b-spec"], ["--gpt-oss-20b-default"],
])
def test_alternate_auxiliary_selectors_and_presets_are_rejected_without_downloading(config, args):
    """Alternate inputs cannot request hidden sidecars/downloads under any typed baseline."""
    with pytest.raises(ValueError, match="model/input authority"):
        driver(config, specMethods=["none"], extraArgs=args)


@pytest.mark.parametrize("args", [
    ["--spec-draft-threads", "2"], ["-ctkd", "f16"], ["-cmoed"], ["-ncmoed", "0"],
    ["--spec-ngram-mod-n-max", "10"], ["--lookup-cache-static", "x"],
    ["--spec-synth-len", "2"], ["--spec_future=whatever"],
])
def test_none_rejects_deferred_and_unknown_speculative_controls(config, args):
    """Disabled mode rejects speculative inputs even when their tuning is not first class."""
    with pytest.raises(ValueError, match="none conflicts"):
        driver(config, specMethods=["none"], extraArgs=args)


def test_method_specific_extras_are_scoped_and_uncertified_and_values_are_not_flags(config):
    """Ngram escape hatches remain possible without certifying ranges or backend support."""
    value = driver(config, specMethods=["ngram-mod"], extraArgs=["--spec-ngram-mod-n-max", "24"])
    assert value.configurationEvidence()["extraArgsCertification"] == "uncertified"
    assert value.configurationEvidence()["speculation"]["uncertifiedExtraArgs"] == ("--spec-ngram-mod-n-max",)
    with pytest.raises(ValueError, match="matching speculative"):
        driver(config, specMethods=["ngram-simple"])
    value = driver(config, specMethods=["none"], extraArgs=["--grammar", "--spec-draft-model"])
    assert value.models["target"].extraArgs[1] == "--spec-draft-model"
    for option in ("--draft", "--draft-n", "--draft-max", "--draft-min", "--draft-n-min", "--spec-ngram-size-n"):
        with pytest.raises(ValueError, match="removed"):
            driver(config, extraArgs=[option + "=3"])


@pytest.mark.parametrize("name,value", [
    ("LLAMA_ARG_SPEC_DRAFT_MODEL", "private.gguf"), ("LLAMA_ARG_SPEC_DRAFT_N_MAX", "0"),
    ("LLAMA_ARG_N_GPU_LAYERS_DRAFT", "auto"), ("LLAMA_ARG_SPEC_DRAFT_P_MIN", "0"),
    ("LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K", "f16"), ("LLAMA_ARG_SPEC_DRAFT_CPU_MOE", "false"),
    ("LLAMA_ARG_NO_SPEC_DRAFT_BACKEND_SAMPLING", "anything"), ("LLAMA_ARG_SPEC_SYNTH_RATES", "0.1"),
    ("LLAMA_ARG_SPEC_FUTURE", "unknown"),
])
def test_none_excludes_relevant_inherited_inputs_including_future_and_negative_aliases(config, monkeypatch, name, value):
    """A CLI none flag alone cannot establish the controlled baseline under inherited draft inputs."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="none conflicts"):
        driver(config, specMethods=["none"])


def test_environment_requires_methods_validates_collisions_and_inherited_auxiliary_path(config, monkeypatch):
    """Inherited local draft inputs require declared methods and participate in evidence, never inference."""
    monkeypatch.setenv("LLAMA_ARG_SPEC_DRAFT_MODEL", auxiliary(config))
    with pytest.raises(ValueError, match="explicit specMethods"):
        driver(config)
    monkeypatch.setenv("LLAMA_ARG_SPEC_TYPE", "draft-simple")
    monkeypatch.setenv("LLAMA_ARG_SPEC_DRAFT_N_MAX", "6")
    monkeypatch.setenv("LLAMA_ARG_SPEC_DRAFT_N_MIN", "5")
    value = driver(config)
    evidence = value.configurationEvidence()
    assert evidence["speculation"]["methods"] == ("draft-simple",)
    assert evidence["speculation"]["resolvedInputs"]["draftMinTokens"] == 5
    assert evidence["inputs"][-1]["role"] == "draft"
    with pytest.raises(ValueError, match="duplicates"):
        driver(config, draftMaxTokens=6)
    for removed in ("LLAMA_ARG_DRAFT_MAX", "LLAMA_ARG_DRAFT_MIN"):
        monkeypatch.setenv(removed, "3")
        with pytest.raises(ValueError, match="removed"):
            impl.LlamaCppDriver(config)
        monkeypatch.delenv(removed)


@pytest.mark.parametrize("name,value", [
    ("LLAMA_ARG_SPEC_DRAFT_HF_REPO", "private/repo"),
    ("LLAMA_ARG_SPEC_DRAFT_N_MAX", "1x"), ("LLAMA_ARG_SPEC_DRAFT_P_MIN", "nan"),
    ("LLAMA_ARG_SPEC_DRAFT_BACKEND_SAMPLING", "maybe"),
])
def test_inherited_alternates_and_malformed_sampling_fail_clearly(config, monkeypatch, name, value):
    """Unknown or invalid inherited authorities do not become certified inputs."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        driver(config, specMethods=["draft-mtp"])


def test_acquisition_snapshot_excludes_later_ambient_draft_inputs_at_launch(config, monkeypatch):
    """Owned/future environment absence remains binding until final lease release."""
    calls = launch(monkeypatch)
    value = driver(config, specMethods=["none"])
    monkeypatch.setenv("LLAMA_ARG_SPEC_TYPE", "draft-simple")
    monkeypatch.setenv("LLAMA_ARG_SPEC_DRAFT_MODEL", auxiliary(config))
    monkeypatch.setenv("LLAMA_ARG_SPEC_FUTURE", "unsafe")
    launchOwnedDriver(value)
    assert len(calls) == 1
    assert calls[0][0][-2:] == ("--spec-type", "none")
    assert not any(configuration.isSpeculationEnv(name) for name in calls[0][1]["env"])
    evidence = value.configurationEvidence()
    assert evidence["launch"]["actualArgv"] == calls[0][0]
    assert evidence["launch"]["automaticConfig"] == value._prepared.automaticConfig
    launchOwnedDriver(value)
    assert len(calls) == 1
    stopOwnedDriver(value)
    assert calls[0][2].stops == 1


def test_automatic_config_blocks_hidden_draft_defaults_and_is_rechecked_before_launch(config, monkeypatch):
    """System/user config files are never silently parsed or allowed to undermine explicit none."""
    calls = launch(monkeypatch)
    value = driver(config, specMethods=["none"])
    path = Path(value._prepared.automaticConfig[0]["path"])
    path.parent.mkdir(parents=True)
    path.write_text("[default]\nspec-draft-model=private.gguf\n", encoding="utf-8")
    with pytest.raises(ValueError, match="config.ini"):
        launchOwnedDriver(value)
    assert not calls
    assert value.configurationEvidence()["automaticConfig"][0]["state"] == "present"
    with pytest.raises(ValueError, match="config.ini"):
        value.checkCapabilities()
    with pytest.raises(ValueError, match="config.ini"):
        driver(config)
    config["models"]["target"].pop("specMethods")
    legacy = driver(config)
    assert legacy.configurationEvidence()["speculation"]["status"] == "omitted-unverified"


def test_shared_lease_identity_covers_draft_environment_and_absence_without_leaks(config, monkeypatch):
    """Auxiliary environment belongs to immutable residency, not whole-service identity."""
    calls = launch(monkeypatch)
    config["models"]["target"].update(specMethods=["draft-simple"], draftModelPath=auxiliary(config))
    monkeypatch.setenv("LLAMA_ARG_SPEC_DRAFT_P_MIN", "0.1")
    shared = SharedComposition()
    first = shared.bind(config)
    monkeypatch.setenv("LLAMA_ARG_SPEC_DRAFT_P_MIN", "0.2")
    second = shared.bind(config)
    try:
        assert first.binding.domain is second.binding.domain and not calls
        assert first.binding.profiles[None] is not second.binding.profiles[None]
        monkeypatch.delenv("LLAMA_ARG_SPEC_DRAFT_P_MIN")
        first.transition()
        assert calls[0][1]["env"]["LLAMA_ARG_SPEC_DRAFT_P_MIN"] == "0.1"
        first.close()
        assert calls[0][2].stops == 0
        second.close()
        assert calls[0][2].stops == 1 and len(calls) == 1
    finally:
        first.close()
        second.close()


def advertised(monkeypatch, missing=()):
    """Supply fake advertised pinned help/version while retaining real gate/flag selection."""
    calls = []
    flags = {flag for item in configuration.OPTIONS for flag in item[0] if flag.startswith("--")} - set(missing)

    def command(path, switch, environment):
        """Record bounded probe requests; this fixture never executes a model/binary."""
        calls.append((switch, environment))
        return {"status": "completed", "exitCode": 0,
                "output": "version (060b2fea4)" if switch == "--version" else " ".join(sorted(flags))}

    monkeypatch.setattr(configuration, "_probeCommand", command)
    return calls


def test_combined_required_gate_privacy_and_lc02_reasoning_still_work(config, monkeypatch):
    """Exercise LC-01 probes/argv plus LC-02 controls/retrieval with explicit speculative config."""
    calls = launch(monkeypatch)
    probes = advertised(monkeypatch)
    config["capabilityCheck"] = "required"
    value = driver(config, specMethods=["draft-simple"], draftModelPath=auxiliary(config),
                   draftDevices=["PRIVATE_DEVICE_SENTINEL"], draftMaxTokens=4, draftSampling="probabilistic",
                   moeCacheMiB=0, serverReasoning="off", extraArgs=["--experimental=uncertified"])
    assert not calls and not probes
    binding = SharedComposition().bind(value._prepared)
    binding.transition()
    value = binding.driver
    requests = []

    class Response:
        """Supply a real SSE path so separate reasoning survives the combined gate."""

        def __enter__(self):
            """Return this response inside the active adapter context."""
            return self

        def __exit__(self, *args):
            """Dispose the synthetic response on parser completion."""
            self.close()

        def close(self):
            """Provide the existing active cancellation/cleanup seam."""
            return None

        def __iter__(self):
            """Emit separate reasoning and answer, followed by real protocol completion."""
            for delta in ({"reasoning_content": "private reason"}, {"content": "answer"}):
                yield b"data: " + json.dumps({"id": "lc-combined", "choices": [{"delta": delta}]}).encode() + b"\n"
            yield b'data: {"id":"lc-combined","choices":[{"delta":{},"finish_reason":"stop"}]}\n'
            yield b"data: [DONE]\n"

    class SlotsResponse(Response):
        """A post-terminal idle observation for the configured owned slot."""

        def read(self):
            """Return one validated idle slot; this fixture has default parallelism one."""
            return b'[{"id":0,"is_processing":false}]'

    def openResponse(request, **kwargs):
        """Capture inference request controls while leaving parser/result processing intact."""
        if request.full_url.endswith("/slots"):
            return SlotsResponse()
        requests.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr(adapter.urlRequest, "urlopen", openResponse)
    result = binding.run(
        query=LlmQuery(formatId="text/plain", payload="question"),
        providerOptions={"reasoningFormat": "deepseek", "chatTemplateKwargs": {"enable_thinking": True}})
    assert result.rawText == "answer" and result.providerMetadata["llamaCppReasoning"]["content"] == "private reason"
    assert requests[0]["reasoning_format"] == "deepseek"
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert len(calls) == 1 and len(probes) == 2
    assert "--spec-draft-model" in value.configurationEvidence()["build"]["requiredFlags"]
    assert value.configurationEvidence()["extraArgsCertification"] == "uncertified"
    public = repr(result.executionProfile.metadata)
    assert "PRIVATE_DRAFT_SENTINEL" not in public and "PRIVATE_DEVICE_SENTINEL" not in public
    assert "PRIVATE_DRAFT_SENTINEL" in repr(value.configurationEvidence())
    binding.close()


def test_missing_draft_flag_blocks_required_launch_without_certifying_model_honor(config, monkeypatch):
    """Combined checking includes the actual requested speculative surface, not just version text."""
    calls = launch(monkeypatch)
    advertised(monkeypatch, missing=["--spec-draft-sampling"])
    config["capabilityCheck"] = "required"
    value = driver(config, specMethods=["draft-mtp"], draftSampling="greedy")
    with pytest.raises(ValueError, match="capability check"):
        launchOwnedDriver(value)
    assert not calls
    assert value.configurationEvidence()["build"]["missingFlags"] == ("--spec-draft-sampling",)


def test_relative_config_discovery_is_bound_to_acquisition_directory(config, monkeypatch, tmp_path):
    """Ambient directory changes cannot hide automatic defaults from the actual child launch directory."""
    monkeypatch.chdir(tmp_path)
    for name in configuration.CONFIG_ENV:
        monkeypatch.setenv(name, "relative-configuration")
    value = driver(config, specMethods=["none"])
    expected = Path(value._prepared.automaticConfig[0]["path"])
    expected.parent.mkdir(parents=True)
    expected.write_text("[default]\nspec-type=draft-mtp\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    with pytest.raises(ValueError, match="config.ini"):
        launchOwnedDriver(value)
    assert value.configurationEvidence()["automaticConfig"][0]["path"] == str(expected)


def test_changed_automatic_config_presence_cannot_reuse_a_shared_lease(config, monkeypatch):
    """New trusted catalogs reject hidden config before acquiring a binding ticket."""
    calls = launch(monkeypatch)
    config["models"]["target"]["specMethods"] = ["none"]
    shared = SharedComposition()
    first = shared.bind(config)
    try:
        path = Path(first.composer.catalog.prepared.automaticConfig[0]["path"])
        path.parent.mkdir(parents=True)
        path.write_text("[default]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="config.ini"):
            shared.bind(config)
        assert not calls and len(first.binding.adapter.owner.leases) == 1
    finally:
        first.close()


@pytest.mark.parametrize("field,sample", [
    ("specMethods", ["none"]), ("draftMaxTokens", 0), ("draftMinTokens", 0),
    ("draftUbatchSize", 0), ("draftModelPath", "draft.gguf"), ("draftDevices", ["none"]),
    ("draftGpuLayers", "auto"), ("draftSampling", "greedy"), ("draftBackendSampling", False),
    ("draftPMin", 0), ("draftPSplit", 0),
])
def test_every_new_control_is_managed_only_at_driver_and_model_scope(field, sample):
    """External endpoints cannot claim startup speculation controls were applied by Actant."""
    for external in ({field: sample}, {"models": {"remote": {"modelPath": "unused", field: sample}}}):
        with pytest.raises(ValueError, match="external server separately"):
            impl.LlamaCppDriver(external)


def test_missing_local_auxiliary_file_fails_without_any_probe_or_launch(config, monkeypatch):
    """Draft inputs never become a download request or a guessed available model."""
    calls = launch(monkeypatch)
    probes = advertised(monkeypatch)
    with pytest.raises(FileNotFoundError, match="local file"):
        driver(config, specMethods=["draft-simple"], draftModelPath="missing-local-draft.gguf")
    assert not calls and not probes


def test_auxiliary_devices_sampling_and_method_changes_affect_profile_identity(config):
    """Profile identity includes actual startup choices, without calling input paths content hashes."""
    first = driver(config, specMethods=["draft-simple"], draftModelPath=auxiliary(config),
                   draftDevices=["GPU0", "GPU1"], draftSampling="greedy")
    firstId = first.configurationEvidence()["profileId"]
    second = driver(config, draftDevices=["GPU1", "GPU0"])
    assert second.configurationEvidence()["profileId"] != firstId
    third = driver(config, draftSampling="probabilistic")
    assert third.configurationEvidence()["profileId"] != second.configurationEvidence()["profileId"]
    fourth = driver(config, specMethods=["draft-mtp"])
    assert fourth.configurationEvidence()["profileId"] != third.configurationEvidence()["profileId"]


def test_deferred_controls_and_unknown_environment_are_uncertified_with_explicit_active_method(config, monkeypatch):
    """Retain experimental escape hatches while keeping them outside validated support claims."""
    monkeypatch.setenv("LLAMA_ARG_SPEC_FUTURE", "private-experiment")
    value = driver(config, specMethods=["draft-mtp"], extraArgs=["--spec-draft-threads", "2"])
    evidence = value.configurationEvidence()
    assert evidence["extraArgsCertification"] == "uncertified"
    assert evidence["speculation"]["uncertifiedEnvironment"] == ("LLAMA_ARG_SPEC_FUTURE",)
    assert evidence["speculation"]["effectiveHonor"] == "unverified"
