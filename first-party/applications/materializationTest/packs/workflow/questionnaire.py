# file: first-party/applications/materializationTest/packs/workflow/questionnaire.py ; version: 1
from __future__ import annotations

from collections.abc import Mapping

_SECTION_IDS = tuple(f"{number:02d}" for number in range(1, 14))


def _requireMapping(value: object, name: str) -> Mapping[str, object]:
    """Require an object-valued questionnaire boundary and return it unchanged."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object.")
    return value


def _requireString(value: object, name: str) -> str:
    """Require one non-blank questionnaire string and preserve its exact contents."""
    if type(value) is not str:
        raise TypeError(f"{name} must be a string.")
    if not value.strip():
        raise ValueError(f"{name} must be a non-blank string.")
    return value


def _requirements(value: object, name: str) -> tuple[str, ...]:
    """Validate and freeze one section's explicit ordered requirement boundaries."""
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list.")
    if not value:
        raise ValueError(f"{name} must contain at least one requirement.")

    result: list[str] = []
    for index, requirement in enumerate(value):
        result.append(_requireString(requirement, f"{name}[{index}]"))
    return tuple(result)


def loadQuestionnaire(ctx, directory: str) -> tuple[str, ...]:
    """Load the benchmark questionnaire from the thirteen semantic section files.

    Args:
        ctx: Active CodeEntry context providing managed JSON reads.
        directory: Application-relative directory containing ``01.json`` through
            ``13.json``.

    Returns:
        All explicit requirement strings in semantic section and file order.

    Raises:
        TypeError: If a section document or member has the wrong runtime type.
        ValueError: If section identity, title, requirements, or requirement
            numbering violates the questionnaire data contract.

    The loader does not discover files or derive question boundaries from prose.
    The fixed section set is part of this benchmark definition. Requirement IDs
    remain embedded in the exact requirement strings used as model evidence.
    """
    directory = _requireString(directory, "questionnaireDirectory")
    questions: list[str] = []
    seenQuestions: set[str] = set()

    for sectionId in _SECTION_IDS:
        path = f"{directory.rstrip('/')}/{sectionId}.json"
        section = _requireMapping(ctx.io.readJson(path), f"Questionnaire section {sectionId}")
        actualSectionId = _requireString(section.get("sectionId"), f"{path}.sectionId")
        if actualSectionId != sectionId:
            raise ValueError(
                f"{path}.sectionId must be {sectionId!r}, not {actualSectionId!r}.",
            )
        _requireString(section.get("title"), f"{path}.title")
        requirements = _requirements(section.get("requirements"), f"{path}.requirements")

        for index, requirement in enumerate(requirements, start=1):
            expectedPrefix = f"{sectionId}.{index:02d}. "
            if not requirement.startswith(expectedPrefix):
                raise ValueError(
                    f"{path}.requirements[{index - 1}] must begin with "
                    f"{expectedPrefix!r}.",
                )
            if requirement in seenQuestions:
                raise ValueError(f"Duplicate questionnaire requirement: {requirement!r}.")
            seenQuestions.add(requirement)
            questions.append(requirement)

    if len(questions) != 117:
        raise ValueError(
            f"Materialization questionnaire must contain exactly 117 requirements, "
            f"not {len(questions)}.",
        )

    return tuple(questions)
