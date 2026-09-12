# file: tests/first_party/materializationTest/test_questionnaire.py ; version: 1
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[3]
_APPLICATION = _ROOT / "first-party" / "applications" / "materializationTest"
_MODULE = _APPLICATION / "packs" / "workflow" / "questionnaire.py"
_SPEC = importlib.util.spec_from_file_location("materializationTestQuestionnaire", _MODULE)
assert _SPEC is not None and _SPEC.loader is not None
questionnaire = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(questionnaire)


class _Io:
    """Read questionnaire fixtures through an application-relative JSON facade."""

    def __init__(self, root: Path) -> None:
        """Bind the fake managed-I/O facade to one application root."""
        self.root = root
        self.paths: list[str] = []

    def readJson(self, path: str) -> object:
        """Read one JSON fixture while recording deterministic access order."""
        self.paths.append(path)
        return json.loads((self.root / path).read_text(encoding="utf-8"))


class _Ctx:
    """Expose only the managed-I/O surface required by the questionnaire loader."""

    def __init__(self, root: Path) -> None:
        """Create a context over one questionnaire fixture tree."""
        self.io = _Io(root)


def test_realQuestionnaire_has_all_117_explicit_requirements_in_section_order() -> None:
    """Real benchmark data loads deterministically without deriving prose boundaries."""
    ctx = _Ctx(_APPLICATION)
    requirements = questionnaire.loadQuestionnaire(ctx, "questionnaire")

    assert len(requirements) == 117
    assert requirements[0].startswith("01.01. ")
    assert requirements[-1].startswith("13.04. ")
    assert len(set(requirements)) == 117
    assert ctx.io.paths == [f"questionnaire/{sectionId:02d}.json" for sectionId in range(1, 14)]


def test_loader_rejects_wrong_section_identity(tmp_path: Path) -> None:
    """A file cannot silently claim another semantic section identity."""
    questionnaireRoot = tmp_path / "questionnaire"
    questionnaireRoot.mkdir()
    for sectionId in range(1, 14):
        source = _APPLICATION / "questionnaire" / f"{sectionId:02d}.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        if sectionId == 4:
            data["sectionId"] = "05"
        (questionnaireRoot / f"{sectionId:02d}.json").write_text(
            json.dumps(data),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="sectionId must be '04'"):
        questionnaire.loadQuestionnaire(_Ctx(tmp_path), "questionnaire")


def test_loader_rejects_requirement_numbering_drift(tmp_path: Path) -> None:
    """Requirement text remains explicitly and correctly identified by its section data."""
    questionnaireRoot = tmp_path / "questionnaire"
    questionnaireRoot.mkdir()
    for sectionId in range(1, 14):
        source = _APPLICATION / "questionnaire" / f"{sectionId:02d}.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        if sectionId == 7:
            data["requirements"][2] = "07.99. Wrong explicit identifier."
        (questionnaireRoot / f"{sectionId:02d}.json").write_text(
            json.dumps(data),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="must begin with '07.03. '"):
        questionnaire.loadQuestionnaire(_Ctx(tmp_path), "questionnaire")
