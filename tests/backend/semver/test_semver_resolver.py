# tests/backed/semver/test_semver_resolver.py
import pytest

from backend.semver.semver import (
    parseSemVerPackVersion,
    parseSemVerPackRequirement,
    SemVerResolver,
)


def _makeCandidates(versions: list[str]) -> list[tuple]:
    return [(parseSemVerPackVersion(v), v) for v in versions]


def test_matchCandidates_no_requirement_picks_highest():
    candidates = _makeCandidates(["1.0.0", "1.5.0", "2.0.0-beta", "2.0.0"])
    req = None

    result = SemVerResolver.matchCandidates(candidates, req)

    assert [c[1] for c in result.candidates] == ["1.0.0", "1.5.0", "2.0.0-beta", "2.0.0"]
    assert [m[1] for m in result.matches] == ["1.0.0", "1.5.0", "2.0.0-beta", "2.0.0"]
    assert result.best is not None
    best_ver, best_payload = result.best
    assert str(best_ver) == "2.0.0"
    assert best_payload == "2.0.0"


def test_matchCandidates_with_requirement_caret():
    candidates = _makeCandidates(["0.9.0", "1.0.0", "1.2.3", "1.5.0", "2.0.0"])
    req = parseSemVerPackRequirement("^1.2.3")

    result = SemVerResolver.matchCandidates(candidates, req)

    # ^1.2.3 => >=1.2.3 and <2.0.0
    assert [m[1] for m in result.matches] == ["1.2.3", "1.5.0"]
    assert result.best is not None
    best_ver, best_payload = result.best
    assert str(best_ver) == "1.5.0"
    assert best_payload == "1.5.0"


def test_matchCandidates_with_requirement_exact():
    candidates = _makeCandidates(["1.0.0", "1.2.3", "1.2.4"])
    req = parseSemVerPackRequirement("1.2.3")

    result = SemVerResolver.matchCandidates(candidates, req)

    assert [m[1] for m in result.matches] == ["1.2.3"]
    assert result.best is not None
    best_ver, best_payload = result.best
    assert str(best_ver) == "1.2.3"
    assert best_payload == "1.2.3"


def test_matchCandidates_no_match():
    candidates = _makeCandidates(["1.0.0", "1.2.3"])
    req = parseSemVerPackRequirement(">2.0.0")

    result = SemVerResolver.matchCandidates(candidates, req)

    assert result.matches == ()
    assert result.best is None
