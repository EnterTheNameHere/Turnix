# file: tests/first_party/materializationTest/test_run.py ; version: 1
from __future__ import annotations

import importlib.util
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
    assert normalized["questionnaireDirectory"] == str(
        (tmp_path / "questionnaire").resolve()
    )
    assert normalized["workspaceDirectory"] == str(
        (tmp_path / "workspace").resolve()
    )
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
