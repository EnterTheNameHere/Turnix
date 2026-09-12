# file: tests/first_party/materializationTest/test_exporter.py ; version: 2
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_EXPORTER = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "materializationTest"
    / "packs"
    / "workflow"
    / "exporter.py"
)
_SPEC = importlib.util.spec_from_file_location("materializationTestExporter", _EXPORTER)
assert _SPEC is not None and _SPEC.loader is not None
exporter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(exporter)


class _Memory:
    """Provide deterministic authoritative values to exporter tests."""

    def __init__(self, values: dict[str, object]) -> None:
        """Bind exact Value-address results for subsequent loads."""
        self.values = values
        self.loads: list[str] = []

    def load(self, address: str) -> object:
        """Record and return one configured authoritative value."""
        self.loads.append(address)
        return self.values.get(address)


class _Io:
    """Provide benchmark-definition inputs and capture one staged JSON export."""

    def __init__(self, prompts: dict[str, object], sections: list[dict[str, object]]) -> None:
        """Bind exact source documents and initialize read/write evidence logs."""
        self.prompts = prompts
        self.sections = sections
        self.reads: list[str] = []
        self.writes: list[tuple[Path, object]] = []

    def readJson(self, path: object) -> object:
        """Return one configured benchmark-definition document exactly."""
        assert type(path) is str
        self.reads.append(path)
        if path == "C:/app/prompts.json":
            return self.prompts
        prefix = "C:/app/questionnaire/"
        assert path.startswith(prefix) and path.endswith(".json")
        sectionId = path[len(prefix):-5]
        return self.sections[int(sectionId) - 1]

    def writeJsonAtomic(self, path: object, value: object) -> None:
        """Record one managed-I/O JSON publication request."""
        self.writes.append((Path(path), value))


class _Ctx:
    """Expose the minimal CodeEntryContext surface used by evidence export."""

    def __init__(self, state: dict[str, object], runs: dict[str, dict[str, object]]) -> None:
        """Construct deterministic memory, identity, configuration, and I/O fakes."""
        values: dict[str, object] = {exporter._RUN_STATE_ADDRESS: state}
        for processingRunId, snapshot in runs.items():
            values[f"processing/{exporter._MEMORY_KEY}/runs/{processingRunId}"] = snapshot
        self.memory = _Memory(values)
        prompts = {"groundingReference": "g", "initialMaterialization": "requirements"}
        sections = [
            {
                "sectionId": f"{number:02d}",
                "title": f"Section {number}",
                "requirements": [f"{number:02d}.01. Requirement"],
            }
            for number in range(1, 14)
        ]
        self.io = _Io(prompts, sections)
        self.identity = SimpleNamespace(applicationId="app-1", applicationRunId="run-9")
        self.config = {
            "strategy": "actant-native",
            "promptsFile": "C:/app/prompts.json",
            "questionnaireDirectory": "C:/app/questionnaire",
            "outputDirectory": "C:/results",
            "llm": {"provider": "llama.cpp", "model": "model"},
        }


def test_processingRunIds_preserves_benchmark_execution_order() -> None:
    """Export follows materialization, self-audit, then questionnaire order."""
    state = {
        "materializationAttempts": [{"processingRunId": "m1"}, {"processingRunId": "m2"}],
        "selfAuditAttempts": [{"processingRunId": "s1"}],
        "questionnaireAnswers": [{"processingRunId": "q1"}, {"processingRunId": "q2"}],
    }

    assert exporter._processingRunIds(state) == ("m1", "m2", "s1", "q1", "q2")


def test_processingRunIds_rejects_duplicate_identity() -> None:
    """One ProcessingRun cannot silently represent two benchmark inference records."""
    state = {
        "materializationAttempts": [{"processingRunId": "same"}],
        "selfAuditAttempts": [],
        "questionnaireAnswers": [{"processingRunId": "same"}],
    }

    with pytest.raises(ValueError, match="Duplicate ProcessingRun"):
        exporter._processingRunIds(state)


def test_testDefinition_preserves_prompt_and_section_source_structure() -> None:
    """Export retains all thirteen questionnaire documents in deterministic order."""
    ctx = _Ctx({}, {})

    definition = exporter._testDefinition(ctx)

    assert definition["prompts"] is ctx.io.prompts
    questionnaire = definition["questionnaire"]
    assert isinstance(questionnaire, dict)
    assert questionnaire["sections"] == ctx.io.sections
    assert ctx.io.reads == [
        "C:/app/prompts.json",
        *[f"C:/app/questionnaire/{number:02d}.json" for number in range(1, 14)],
    ]


def test_export_projects_authoritative_state_and_exact_processing_runs() -> None:
    """Portable evidence copies shared exact-query evidence instead of rebuilding it."""
    state = {
        "materializationOutcome": "clean",
        "selfAuditOutcome": "clean",
        "questionnaireOutcome": "completed",
        "materializationAttempts": [{"processingRunId": "m1"}],
        "selfAuditAttempts": [{"processingRunId": "s1"}],
        "questionnaireAnswers": [{"processingRunId": "q1"}],
    }
    runs = {
        "m1": {"processingRunId": "m1", "query": {"payload": "exact materialization"}},
        "s1": {"processingRunId": "s1", "query": {"payload": "exact self audit"}},
        "q1": {"processingRunId": "q1", "query": {"payload": "exact question"}},
    }
    ctx = _Ctx(state, runs)

    result = exporter._export(ctx, None)

    assert result["processingRunCount"] == 3
    assert result["questionnaireOutcome"] == "completed"
    assert len(ctx.io.writes) == 1
    path, evidence = ctx.io.writes[0]
    assert path == Path("C:/results/app-1/run-9/evidence.json")
    assert evidence["formatId"] == "materialization-test.evidence@1"
    assert evidence["workflowState"] is state
    assert evidence["processingRuns"] == [runs["m1"], runs["s1"], runs["q1"]]
    assert evidence["testDefinition"] == {
        "prompts": ctx.io.prompts,
        "questionnaire": {"sections": ctx.io.sections},
    }
    assert evidence["configuration"] == ctx.config


def test_export_rejects_missing_processing_run_evidence() -> None:
    """A portable export cannot silently omit an inference referenced by state."""
    state = {
        "materializationAttempts": [{"processingRunId": "missing"}],
        "selfAuditAttempts": [],
        "questionnaireAnswers": [],
    }
    ctx = _Ctx(state, {})

    with pytest.raises(RuntimeError, match="missing or invalid"):
        exporter._export(ctx, None)
