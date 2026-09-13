# file: tests/first_party/materializationTest/test_progress_evidence.py ; version: 1
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_APPLICATION = Path(__file__).parents[3] / "first-party" / "applications" / "materializationTest"
_PROGRESS_ENTRY = _APPLICATION / "packs" / "workflow" / "progressCodeEntry.py"
_PROGRESS_EXPORTER = _APPLICATION / "packs" / "workflow" / "progressExporter.py"


def _load(path: Path, name: str):
    """Load one application CodeEntry directly for focused adapter tests."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


progress = _load(_PROGRESS_ENTRY, "materializationTestProgressCodeEntry")
terminal = _load(_PROGRESS_EXPORTER, "materializationTestProgressExporter")


class _Io:
    """Provide deterministic definition reads and capture atomic evidence writes."""

    def __init__(self) -> None:
        self.writes: list[tuple[Path, dict[str, object]]] = []
        self.current: dict[str, object] | None = None

    def readJson(self, path) -> object:
        if str(path).endswith("evidence.json"):
            assert self.current is not None
            return dict(self.current)
        if str(path).endswith("prompts.json"):
            return {"groundingReference": "grounding"}
        return {"sectionId": Path(str(path)).stem, "title": "section", "requirements": []}

    def writeJsonAtomic(self, path: Path, value: dict[str, object]) -> None:
        self.current = value
        self.writes.append((path, value))


class _Memory:
    """Expose completed ProcessingRun snapshots by their canonical address."""

    def __init__(self) -> None:
        self.values = {
            "processing/materializationtest/runs/run-1": {"processingRunId": "run-1"},
            "processing/materializationtest/runs/run-2": {"processingRunId": "run-2"},
        }

    def load(self, address: str) -> object:
        return self.values.get(address)


def _ctx(applicationRunId: str = "application-run-1"):
    io = _Io()
    return SimpleNamespace(
        config={
            "outputDirectory": "C:/results",
            "promptsFile": "prompts.json",
            "questionnaireDirectory": "questionnaire",
        },
        identity=SimpleNamespace(
            applicationId="application-1",
            applicationRunId=applicationRunId,
        ),
        io=io,
        memory=_Memory(),
    )


def test_progressEvidence_accumulates_only_completed_processing_runs_atomically() -> None:
    """Each safe inference boundary replaces evidence with all completed runs so far."""
    ctx = _ctx()
    progress._completedProcessingRunIdsByApplicationRun.clear()

    progress._writeProgressEvidence(ctx, processingRunId="run-1")
    first = ctx.io.writes[-1][1]
    assert first["runStatus"] == {
        "state": "in-progress",
        "restorable": False,
        "completedProcessingRunCount": 1,
        "lastCompletedProcessingRunId": "run-1",
    }
    assert [item["processingRunId"] for item in first["processingRuns"]] == ["run-1"]
    assert first["workflowState"] is None

    progress._writeProgressEvidence(ctx, processingRunId="run-2")
    second = ctx.io.writes[-1][1]
    assert second["runStatus"]["completedProcessingRunCount"] == 2
    assert [item["processingRunId"] for item in second["processingRuns"]] == ["run-1", "run-2"]
    assert ctx.io.writes[-1][0] == Path(
        "C:/results/application-1/application-run-1/evidence.json"
    )


def test_progressEvidence_isolates_process_local_ledgers_by_application_run() -> None:
    """Concurrent or sequential ApplicationRuns cannot inherit another run's evidence list."""
    progress._completedProcessingRunIdsByApplicationRun.clear()
    firstCtx = _ctx("run-a")
    secondCtx = _ctx("run-b")

    progress._writeProgressEvidence(firstCtx, processingRunId="run-1")
    progress._writeProgressEvidence(secondCtx, processingRunId="run-2")

    assert [item["processingRunId"] for item in firstCtx.io.current["processingRuns"]] == ["run-1"]
    assert [item["processingRunId"] for item in secondCtx.io.current["processingRuns"]] == ["run-2"]


def test_terminalExporter_marks_normal_export_completed(monkeypatch) -> None:
    """A successful terminal export replaces in-progress status with completed status."""
    ctx = _ctx()
    path = Path("C:/results/application-1/application-run-1/evidence.json")

    def fakeExport(_ctx, _payload):
        _ctx.io.writeJsonAtomic(
            path,
            {
                "formatId": "materialization-test.evidence@1",
                "processingRuns": [
                    {"processingRunId": "run-1"},
                    {"processingRunId": "run-2"},
                ],
            },
        )
        return {"path": str(path)}

    monkeypatch.setattr(terminal._impl, "_export", fakeExport)
    monkeypatch.setattr(terminal._impl, "_exportPath", lambda _ctx: path)

    result = terminal._export(ctx, None)

    assert result == {"path": str(path)}
    assert ctx.io.current["runStatus"] == {
        "state": "completed",
        "restorable": False,
        "completedProcessingRunCount": 2,
    }
