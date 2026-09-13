# file: tests/first_party/materializationTest/test_run.py ; version: 2
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_RUN = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "materializationTest"
    / "run.py"
)
_SPEC = importlib.util.spec_from_file_location("materializationTestRun", _RUN)
assert _SPEC is not None and _SPEC.loader is not None
run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run)


class _JsonIo:
    """Minimal JSON reader used to exercise layered configuration loading."""

    def readJson(self, path: Path) -> object:
        """Read one UTF-8 JSON document from the requested test path."""
        return json.loads(path.read_text(encoding="utf-8"))


def test_mergeConfig_recursively_extends_nested_registries() -> None:
    """Local nested mappings extend defaults while overriding selected leaves."""
    base = {
        "processTools": {},
        "llm": {"provider": "llama.cpp", "model": "model-1"},
        "llamaCpp": {
            "models": {
                "model-1": {"modelPath": "one.gguf", "threads": 12},
            },
        },
    }
    local = {
        "processTools": {"ruff": "ruff.exe", "ty": "ty.exe"},
        "llm": {"model": "model-2"},
        "llamaCpp": {
            "models": {
                "model-2": {"modelPath": "two.gguf"},
            },
        },
    }

    merged = run._mergeConfig(base, local)

    assert merged == {
        "processTools": {"ruff": "ruff.exe", "ty": "ty.exe"},
        "llm": {"provider": "llama.cpp", "model": "model-2"},
        "llamaCpp": {
            "models": {
                "model-1": {"modelPath": "one.gguf", "threads": 12},
                "model-2": {"modelPath": "two.gguf"},
            },
        },
    }
    assert base["processTools"] == {}
    assert local["llamaCpp"]["models"] == {"model-2": {"modelPath": "two.gguf"}}


def test_mergeConfig_replaces_non_mapping_values_atomically() -> None:
    """Lists, scalars, and null shadow defaults instead of receiving ad-hoc merges."""
    base = {"items": [1, 2], "enabled": True, "value": {"nested": 1}}
    local = {"items": [3], "enabled": False, "value": None}

    merged = run._mergeConfig(base, local)

    assert merged == {"items": [3], "enabled": False, "value": None}


def test_loadConfig_automatically_applies_sibling_local_shadow(tmp_path: Path) -> None:
    """Selecting config.json implicitly overlays config.local.json when present."""
    configPath = tmp_path / "config.json"
    localPath = tmp_path / "config.local.json"
    configPath.write_text(
        json.dumps({"llm": {"provider": "llama.cpp", "model": "model-1"}}),
        encoding="utf-8",
    )
    localPath.write_text(
        json.dumps({"llm": {"model": "model-2"}}),
        encoding="utf-8",
    )

    loaded = run._loadConfig(_JsonIo(), configPath)

    assert loaded == {"llm": {"provider": "llama.cpp", "model": "model-2"}}


def test_loadConfig_accepts_absent_local_shadow(tmp_path: Path) -> None:
    """A repository default configuration remains usable without a local file."""
    configPath = tmp_path / "config.json"
    configPath.write_text(json.dumps({"strategy": "actant-native"}), encoding="utf-8")

    loaded = run._loadConfig(_JsonIo(), configPath)

    assert loaded == {"strategy": "actant-native"}


def test_normalizePaths_resolves_all_application_input_and_output_paths(
    tmp_path: Path,
) -> None:
    """Relative benchmark data/workspace/output paths resolve beside config."""
    config = {
        "promptsFile": "prompts.json",
        "questionnaireDirectory": "questionnaire",
        "workspaceDirectory": "workspace",
        "outputDirectory": "results",
    }

    normalized = run._normalizePaths(config, configDirectory=tmp_path)

    assert normalized["promptsFile"] == str((tmp_path / "prompts.json").resolve())
    assert normalized["questionnaireDirectory"] == str((tmp_path / "questionnaire").resolve())
    assert normalized["workspaceDirectory"] == str((tmp_path / "workspace").resolve())
    assert normalized["outputDirectory"] == str((tmp_path / "results").resolve())
    assert config["questionnaireDirectory"] == "questionnaire"


def test_normalizePaths_resolves_host_process_tools_relative_to_config(
    tmp_path: Path,
) -> None:
    """Configured analyzer executables become absolute before runtime creation."""
    config = {
        "processTools": {
            "ruff": "tools/ruff.exe",
            "ty": "tools/ty.exe",
        },
    }

    normalized = run._normalizePaths(config, configDirectory=tmp_path)

    assert normalized["processTools"] == {
        "ruff": str((tmp_path / "tools" / "ruff.exe").resolve()),
        "ty": str((tmp_path / "tools" / "ty.exe").resolve()),
    }
    assert config["processTools"] == {
        "ruff": "tools/ruff.exe",
        "ty": "tools/ty.exe",
    }


def test_normalizePaths_preserves_absolute_process_tool_path(tmp_path: Path) -> None:
    """Already absolute host-authorized analyzer paths retain their identity."""
    executable = (tmp_path / "ruff.exe").resolve()
    config = {"processTools": {"ruff": str(executable)}}

    normalized = run._normalizePaths(config, configDirectory=tmp_path / "config")

    assert normalized["processTools"] == {"ruff": str(executable)}
