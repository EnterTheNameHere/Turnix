from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "evilBirthdayAnalysis"
    / "packs"
    / "results"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayResultsCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
results = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(results)


class _Io:
    def __init__(self):
        self.writes = []

    def writeJsonAtomic(self, path, payload):
        self.writes.append((path, payload))


class _Ctx:
    def __init__(self, outputDirectory="results"):
        self.config = {"outputDirectory": outputDirectory}
        self.io = _Io()


def test_save_reports_same_managed_relative_path_it_writes():
    ctx = _Ctx()
    payload = {"resultId": "result-1", "value": 1}

    saved = results._save(ctx, payload)

    assert ctx.io.writes == [(Path("results") / "result-1.json", payload)]
    assert saved == {"resultId": "result-1", "path": str(Path("results") / "result-1.json")}


def test_save_rejects_blank_result_and_output_identity():
    with pytest.raises(ValueError, match="non-empty resultId"):
        results._save(_Ctx(), {"resultId": ""})

    with pytest.raises(ValueError, match="non-blank"):
        results._save(_Ctx("  "), {"resultId": "result-1"})
