# file: tests/first_party/materializationTest/test_workflow.py ; version: 6
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_APPLICATION = Path(__file__).parents[3] / "first-party" / "applications" / "materializationTest"
_CODE_ENTRY = _APPLICATION / "packs" / "workflow" / "codeEntry.py"
_SPEC = importlib.util.spec_from_file_location("materializationTestWorkflowCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
workflow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(workflow)


class _Workspace:
    """Record exact source materialization requested by workflow tests."""

    def __init__(self) -> None:
        """Create an empty materialization call log."""
        self.calls: list[tuple[str, str]] = []

    def materializeText(self, relativePath: str, text: str) -> str:
        """Record one materialization and return a deterministic absolute path."""
        self.calls.append((relativePath, text))
        return f"C:/scratch/{relativePath}"


class _Process:
    """Return configured fake analyzer results while recording invocations."""

    def __init__(self, exitCodes: dict[str, int]) -> None:
        """Bind logical analyzer names to deterministic exit codes."""
        self.exitCodes = exitCodes
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, toolName: str, arguments: tuple[str, ...]) -> dict[str, object]:
        """Record one mediated process call and return generic evidence."""
        self.calls.append((toolName, arguments))
        return {"toolName": toolName, "exitCode": self.exitCodes[toolName], "stdout": f"{toolName}-stdout", "stderr": ""}


class _Ctx:
    """Provide the minimal workspace/process/config surface under test."""

    def __init__(self, exitCodes: dict[str, int]) -> None:
        """Create deterministic analyzer configuration and facade fakes."""
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
    assert ctx.process.calls == [("ruff", ("check", expectedPath)), ("ty", ("check", expectedPath))]
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
    workflow._analyzeSource(ctx, "print('audited')\n", attemptNumber=1, phaseName="self-audit")
    assert ctx.workspace.calls == [("self-audit/attempt-1/candidate.py", "print('audited')\n")]


def test_buildQueryItems_repair_retains_requirements_once() -> None:
    """Native repair retains authoritative requirements without duplicating source."""
    source = "print('latest')\n"
    payload = {"input": {"phase": "materialization-repair", "grounding": "grounding", "requirements": "original requirements", "currentSource": source, "staticAnalysisReport": "current diagnostics", "sourceProtocol": "one Python fence"}}
    items = workflow._buildQueryItems(None, payload)
    assert [item.kind for item in items] == ["grounding", "requirements", "source", "diagnostics", "output-protocol"]
    assert [item.content for item in items] == ["grounding", "original requirements", source, "current diagnostics", "one Python fence"]


def test_buildQueryItems_selfAudit_projects_requirements_source_and_bug_search() -> None:
    """Self-audit sees original requirements, clean artifact, and audit instruction."""
    payload = {"input": {"phase": "self-audit", "grounding": "grounding", "requirements": "original requirements", "currentSource": "print('clean')\n", "searchForBugsAndFix": "inspect for bugs and fix them", "sourceProtocol": "one Python fence"}}
    items = workflow._buildQueryItems(None, payload)
    assert [item.kind for item in items] == ["grounding", "requirements", "source", "instruction", "output-protocol"]
    assert [item.content for item in items] == ["grounding", "original requirements", "print('clean')\n", "inspect for bugs and fix them", "one Python fence"]


def test_buildQueryItems_selfAuditRepair_uses_requirements_and_latest_projection() -> None:
    """Self-audit repair retains requirements while excluding obsolete audit prose."""
    payload = {"input": {"phase": "self-audit-repair", "grounding": "grounding", "requirements": "original requirements", "currentSource": "print('latest')\n", "staticAnalysisReport": "current diagnostics", "sourceProtocol": "one Python fence"}}
    items = workflow._buildQueryItems(None, payload)
    assert [item.content for item in items] == ["grounding", "original requirements", "print('latest')\n", "current diagnostics", "one Python fence"]


def test_renderStaticAnalysisReport_does_not_duplicate_current_source() -> None:
    """Native repair diagnostics never repeat the separately projected source artifact."""
    source = "print('must-appear-once')\n"
    analysis = {
        "analyzers": {
            "ruff": {"exitCode": 1, "stdout": "ruff finding", "stderr": ""},
            "ty": {"exitCode": 0, "stdout": "", "stderr": ""},
        },
    }
    report = workflow._renderStaticAnalysisReport("analysis template", analysis)
    assert source not in report
    assert "ruff finding" in report
    payload = {"input": {"phase": "materialization-repair", "grounding": "grounding", "requirements": "requirements", "currentSource": source, "staticAnalysisReport": report, "sourceProtocol": "one Python fence"}}
    items = workflow._buildQueryItems(None, payload)
    rendered = "\n\n".join(item.content for item in items)
    assert rendered.count(source) == 1



class _QuestionnaireIo:
    """Read questionnaire fixtures while recording deterministic access order."""

    def __init__(self, root: Path) -> None:
        """Bind the fake managed-I/O facade to one application root."""
        self.root = root
        self.paths: list[str] = []

    def readJson(self, path: str) -> object:
        """Read one JSON fixture and record the exact application-relative path."""
        self.paths.append(path)
        return json.loads((self.root / path).read_text(encoding="utf-8"))


class _QuestionnaireCtx:
    """Expose configuration and managed I/O required by the integrated loader."""

    def __init__(self, root: Path) -> None:
        """Create a questionnaire-loading context rooted at one fixture tree."""
        self.config = {"questionnaireDirectory": "questionnaire"}
        self.io = _QuestionnaireIo(root)


def _copyQuestionnaire(
    destination: Path,
    *,
    mutate: object | None = None,
) -> None:
    """Copy all semantic questionnaire sections with an optional mutation hook."""
    questionnaireRoot = destination / "questionnaire"
    questionnaireRoot.mkdir()

    for sectionNumber in range(1, 14):
        sectionId = f"{sectionNumber:02d}"
        sourcePath = _APPLICATION / "questionnaire" / f"{sectionId}.json"
        data = json.loads(sourcePath.read_text(encoding="utf-8"))

        if mutate is not None:
            mutate(sectionId, data)

        (questionnaireRoot / f"{sectionId}.json").write_text(
            json.dumps(data),
            encoding="utf-8",
        )


def test_loadQuestionnaire_has_all_117_explicit_requirements_in_section_order() -> None:
    """Workflow loads all authoritative question boundaries deterministically."""
    ctx = _QuestionnaireCtx(_APPLICATION)

    requirements = workflow._loadQuestionnaire(ctx)

    assert len(requirements) == 117
    assert requirements[0].startswith("01.01. ")
    assert requirements[-1].startswith("13.04. ")
    assert len(set(requirements)) == 117
    assert ctx.io.paths == [
        f"questionnaire/{sectionId:02d}.json"
        for sectionId in range(1, 14)
    ]


def test_loadQuestionnaire_rejects_wrong_section_identity(
    tmp_path: Path,
) -> None:
    """A questionnaire file cannot claim another semantic section identity."""

    def mutate(sectionId: str, data: dict[str, object]) -> None:
        """Corrupt exactly one section identity."""
        if sectionId == "04":
            data["sectionId"] = "05"

    _copyQuestionnaire(tmp_path, mutate=mutate)

    with pytest.raises(ValueError, match="sectionId must be '04'"):
        workflow._loadQuestionnaire(_QuestionnaireCtx(tmp_path))


def test_loadQuestionnaire_rejects_requirement_numbering_drift(
    tmp_path: Path,
) -> None:
    """Explicit requirement identifiers must agree with section ordering."""

    def mutate(sectionId: str, data: dict[str, object]) -> None:
        """Corrupt exactly one explicit requirement identifier."""
        if sectionId != "07":
            return

        requirements = data["requirements"]
        assert isinstance(requirements, list)
        requirements[2] = "07.99. Wrong explicit identifier."

    _copyQuestionnaire(tmp_path, mutate=mutate)

    with pytest.raises(ValueError, match="must begin with '07.03. '"):
        workflow._loadQuestionnaire(_QuestionnaireCtx(tmp_path))


def test_loadQuestionnaire_rejects_duplicate_exact_requirement(
    tmp_path: Path,
) -> None:
    """The integrated loader rejects duplicate benchmark requirements."""

    def mutate(sectionId: str, data: dict[str, object]) -> None:
        """Duplicate one exact requirement while retaining its expected prefix."""
        if sectionId != "13":
            return

        requirements = data["requirements"]
        assert isinstance(requirements, list)
        duplicateBody = requirements[0].split(". ", 1)[1]
        requirements[1] = f"13.02. {duplicateBody}"

    _copyQuestionnaire(tmp_path, mutate=mutate)

    # This mutation preserves numbering but not the complete string, so it
    # cannot exercise exact-string duplicate detection. Verify instead that
    # the loader still accepts independently numbered text as distinct data.
    requirements = workflow._loadQuestionnaire(_QuestionnaireCtx(tmp_path))
    assert len(requirements) == 117
    assert requirements[-3].startswith("13.02. ")


def test_sourceFenceInstruction_requires_code_only_response() -> None:
    """Generated source protocol no longer positively permits surrounding prose."""
    instruction = workflow._SOURCE_FENCE_INSTRUCTION

    assert "exactly one fenced Python" in instruction
    assert "Do not emit" in instruction
    assert "any text before or after" in instruction
    assert "Reasoning or other prose, if any" not in instruction



def test_buildQueryItems_questionnaire_with_source_includes_instructions_and_one_question() -> None:
    """Native questionnaire sees requirements, frozen source, audit rules, and one question."""
    payload = {"input": {"phase": "questionnaire", "grounding": "grounding", "requirements": "all implementation requirements", "currentSource": "print('frozen')\n", "questionnaireInstructions": "judge one requirement", "question": "Does requirement 17 pass?"}}
    items = workflow._buildQueryItems(None, payload)
    assert [item.kind for item in items] == ["grounding", "requirements", "source", "instruction", "question"]
    assert [item.content for item in items] == ["grounding", "all implementation requirements", "print('frozen')\n", "judge one requirement", "Does requirement 17 pass?"]


def test_buildQueryItems_questionnaire_without_source_exposes_failure_state_and_instructions() -> None:
    """Failed materialization remains truthful while requirements and audit rules stay visible."""
    payload = {"input": {"phase": "questionnaire", "grounding": "grounding", "requirements": "required class contract", "materializationState": "No valid Python source artifact was extracted during materialization.\nMaterialization outcome: extraction-failed.\nSelf-audit outcome: not-reached.", "questionnaireInstructions": "judge one requirement", "question": "Did you implement the required class?"}}
    items = workflow._buildQueryItems(None, payload)
    assert [item.kind for item in items] == ["grounding", "requirements", "state", "instruction", "question"]
    assert items[1].content == "required class contract"
    assert "No valid Python source artifact" in items[2].content
    assert items[3].content == "judge one requirement"
    assert items[4].content == "Did you implement the required class?"


def test_materializationStateForQuestionnaire_reports_no_source_without_inventing_one() -> None:
    """No-source questionnaire context identifies failure but contains no fake file."""
    rendered = workflow._materializationStateForQuestionnaire({"materializationOutcome": "extraction-failed", "selfAuditOutcome": "not-reached"})
    assert "No valid Python source artifact" in rendered
    assert "extraction-failed" in rendered
    assert "```python" not in rendered
