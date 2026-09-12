# file: tests/first_party/materializationTest/test_workflow.py ; version: 3
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "materializationTest"
    / "packs"
    / "workflow"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("materializationTestWorkflowCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
workflow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(workflow)


class _Workspace:
    """Records exact source materialization requested by workflow tests."""

    def __init__(self) -> None:
        """Creates an empty materialization call log."""
        self.calls: list[tuple[str, str]] = []

    def materializeText(self, relativePath: str, text: str) -> str:
        """Records one materialization and returns a deterministic absolute path."""
        self.calls.append((relativePath, text))
        return f"C:/scratch/{relativePath}"


class _Process:
    """Returns configured fake analyzer results while recording invocations."""

    def __init__(self, exitCodes: dict[str, int]) -> None:
        """Binds logical analyzer names to deterministic exit codes."""
        self.exitCodes = exitCodes
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, toolName: str, arguments: tuple[str, ...]) -> dict[str, object]:
        """Records one mediated process call and returns generic evidence."""
        self.calls.append((toolName, arguments))
        return {
            "toolName": toolName,
            "exitCode": self.exitCodes[toolName],
            "stdout": f"{toolName}-stdout",
            "stderr": "",
        }


class _Ctx:
    """Provides the minimal workspace/process/config surface under test."""

    def __init__(self, exitCodes: dict[str, int]) -> None:
        """Creates deterministic analyzer configuration and facade fakes."""
        self.config = {
            "analyzers": {
                "ruff": {"toolId": "ruff", "arguments": ["check", "{source}"]},
                "ty": {"toolId": "ty", "arguments": ["check", "{source}"]},
            },
        }
        self.workspace = _Workspace()
        self.process = _Process(exitCodes)


def test_extractPythonSource_preserves_exact_body_with_surrounding_prose() -> None:
    """Valid extraction preserves source newlines and permits ordinary prose."""
    response = "Reasoning first.\r\n```python\r\nprint('x')\r\n\r\n```\r\nDone."

    assert workflow._extractPythonSource(response) == "print('x')\r\n\r\n"


@pytest.mark.parametrize(
    ("response", "message"),
    (
        ("no source", "found 0"),
        ("```\nx = 1\n```", "python info string"),
        ("```javascript\nx = 1\n```", "python info string"),
        ("```python\n```", "empty or whitespace-only"),
        ("```python\n   \n```", "empty or whitespace-only"),
        ("```python\nx = 1\n", "not closed"),
        ("```python\nx = 1\n```\n```text\ny\n```", "more than one"),
        ("```python\nx = 1\n```\n```broken", "additional malformed"),
    ),
)
def test_extractPythonSource_rejects_protocol_violations(response: str, message: str) -> None:
    """Every malformed source-response shape is a model-output failure."""
    with pytest.raises(workflow.SourceExtractionError, match=message):
        workflow._extractPythonSource(response)


def test_analyzeSource_uses_same_exact_workspace_artifact_for_ruff_and_ty() -> None:
    """Both analyzers consume one immediate exact ephemeral source artifact."""
    ctx = _Ctx({"ruff": 0, "ty": 0})
    source = "print('exact')\r\n"

    result = workflow._analyzeSource(ctx, source, attemptNumber=2)

    assert ctx.workspace.calls == [("materialization/attempt-2/candidate.py", source)]
    expectedPath = "C:/scratch/materialization/attempt-2/candidate.py"
    assert ctx.process.calls == [
        ("ruff", ("check", expectedPath)),
        ("ty", ("check", expectedPath)),
    ]
    assert result["clean"] is True


def test_analyzeSource_treats_nonzero_exit_as_findings_not_execution_failure() -> None:
    """A completed analyzer with findings makes the candidate dirty normally."""
    ctx = _Ctx({"ruff": 1, "ty": 0})

    result = workflow._analyzeSource(ctx, "x = missing\n", attemptNumber=1)

    assert result["clean"] is False
    assert result["analyzers"]["ruff"]["exitCode"] == 1
    assert result["analyzers"]["ty"]["exitCode"] == 0


def test_analyzeSource_selfAudit_uses_distinct_workspace_namespace() -> None:
    """Independent self-audit attempts cannot collide with initial attempts."""
    ctx = _Ctx({"ruff": 0, "ty": 0})

    workflow._analyzeSource(
        ctx,
        "print('audited')\n",
        attemptNumber=1,
        phaseName="self-audit",
    )

    assert ctx.workspace.calls == [
        ("self-audit/attempt-1/candidate.py", "print('audited')\n"),
    ]


def test_buildQueryItems_selfAudit_projects_clean_source_and_bug_search() -> None:
    """Self-audit inference sees the clean artifact and historical audit instruction."""
    payload = {
        "input": {
            "phase": "self-audit",
            "grounding": "grounding",
            "currentSource": "print('clean')\n",
            "searchForBugsAndFix": "inspect for bugs and fix them",
            "sourceProtocol": "one Python fence",
        },
    }

    items = workflow._buildQueryItems(None, payload)

    assert [item.kind for item in items] == [
        "grounding",
        "source",
        "instruction",
        "output-protocol",
    ]
    assert [item.content for item in items] == [
        "grounding",
        "print('clean')\n",
        "inspect for bugs and fix them",
        "one Python fence",
    ]


def test_buildQueryItems_selfAuditRepair_uses_only_latest_repair_projection() -> None:
    """Self-audit repair excludes obsolete audit prose and previous candidates."""
    payload = {
        "input": {
            "phase": "self-audit-repair",
            "grounding": "grounding",
            "currentSource": "print('latest')\n",
            "staticAnalysisReport": "current diagnostics",
            "sourceProtocol": "one Python fence",
        },
    }

    items = workflow._buildQueryItems(None, payload)

    assert [item.content for item in items] == [
        "grounding",
        "print('latest')\n",
        "current diagnostics",
        "one Python fence",
    ]


def test_requireQuestions_preserves_order_and_exact_question_text() -> None:
    """Question boundaries are explicit data and question contents stay exact."""
    questions = ["01. First?\r\nExplain.", "02. Second?"]

    assert workflow._requireQuestions(questions, "questionnaire") == tuple(questions)


def test_requireQuestions_rejects_implicit_or_blank_questionnaire_shapes() -> None:
    """The harness never guesses question boundaries from free-form text."""
    with pytest.raises(TypeError, match="must be a list"):
        workflow._requireQuestions("01. First?\n02. Second?", "questionnaire")
    with pytest.raises(ValueError, match="at least one"):
        workflow._requireQuestions([], "questionnaire")
    with pytest.raises(ValueError, match=r"questionnaire\[1\]"):
        workflow._requireQuestions(["valid", "   "], "questionnaire")


def test_buildQueryItems_questionnaire_with_source_uses_one_question() -> None:
    """Native questionnaire projection contains one question and frozen source."""
    payload = {
        "input": {
            "phase": "questionnaire",
            "grounding": "grounding",
            "currentSource": "print('frozen')\n",
            "question": "Does requirement 17 pass?",
        },
    }

    items = workflow._buildQueryItems(None, payload)

    assert [item.kind for item in items] == ["grounding", "source", "question"]
    assert [item.content for item in items] == [
        "grounding",
        "print('frozen')\n",
        "Does requirement 17 pass?",
    ]


def test_buildQueryItems_questionnaire_without_source_exposes_failure_state() -> None:
    """A failed materialization is described truthfully instead of using fake source."""
    payload = {
        "input": {
            "phase": "questionnaire",
            "grounding": "grounding",
            "materializationState": (
                "No valid Python source artifact was extracted during materialization.\n"
                "Materialization outcome: extraction-failed.\n"
                "Self-audit outcome: not-reached."
            ),
            "question": "Did you implement the required class?",
        },
    }

    items = workflow._buildQueryItems(None, payload)

    assert [item.kind for item in items] == ["grounding", "state", "question"]
    assert "No valid Python source artifact" in items[1].content
    assert items[2].content == "Did you implement the required class?"


def test_materializationStateForQuestionnaire_reports_no_source_without_inventing_one() -> None:
    """No-source questionnaire context identifies failure but contains no fake file."""
    rendered = workflow._materializationStateForQuestionnaire(
        {
            "materializationOutcome": "extraction-failed",
            "selfAuditOutcome": "not-reached",
        },
    )

    assert "No valid Python source artifact" in rendered
    assert "extraction-failed" in rendered
    assert "```python" not in rendered
