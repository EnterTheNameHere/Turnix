# tests/semver/test_semver_requirement.py
import pytest

from backend.semver.semver import (
    parseSemVerPackVersion,
    SemVerPackVersion,
    SemVerPackRequirement,
    parseSemVerPackRequirement,
    versionSatisfiesRequirement,
)


def _matches(req_str: str | None, candidates: list[str]) -> list[str]:
    req = parseSemVerPackRequirement(req_str)
    vers = [parseSemVerPackVersion(vs) for vs in candidates]
    return [str(v) for v in vers if versionSatisfiesRequirement(v, req)]


def test_requirement_wildcard_none():
    v = parseSemVerPackVersion("1.2.3")
    assert versionSatisfiesRequirement(v, None)


@pytest.mark.parametrize("raw", [None, "", "   ", "*"])
def test_requirement_parse_any(raw):
    req = parseSemVerPackRequirement(raw)
    assert req is None


def test_requirement_exact_version():
    req = parseSemVerPackRequirement("1.2.3")
    assert req is not None
    v_good = parseSemVerPackVersion("1.2.3")
    v_bad = parseSemVerPackVersion("1.2.4")
    assert versionSatisfiesRequirement(v_good, req)
    assert not versionSatisfiesRequirement(v_bad, req)


def test_requirement_basic_inequalities():
    req = parseSemVerPackRequirement(">=1.2.0 <2.0.0")
    matched = _matches(">=1.2.0 <2.0.0", ["1.1.9", "1.2.0", "1.5.0", "2.0.0"])
    assert matched == ["1.2.0", "1.5.0"]


def test_requirement_caret_semantics():
    # ^1.2.3  => >=1.2.3 and <2.0.0
    matched = _matches("^1.2.3", ["1.2.3", "1.4.0", "2.0.0", "0.9.0"])
    assert matched == ["1.2.3", "1.4.0"]

    # ^0.2.3  => >=0.2.3 and <0.3.0
    matched_0x = _matches("^0.2.3", ["0.2.3", "0.2.9", "0.3.0", "1.0.0"])
    assert matched_0x == ["0.2.3", "0.2.9"]

    # ^0.0.3  => >=0.0.3 and <0.0.4
    matched_003 = _matches("^0.0.3", ["0.0.2", "0.0.3", "0.0.4"])
    assert matched_003 == ["0.0.3"]


def test_requirement_tilde_semantics():
    # ~1.2.3 => >=1.2.3 and <1.3.0
    matched = _matches("~1.2.3", ["1.2.3", "1.2.9", "1.3.0", "2.0.0"])
    assert matched == ["1.2.3", "1.2.9"]

    # ~1 => >=1.0.0 and <2.0.0
    matched_major = _matches("~1", ["0.9.9", "1.0.0", "1.5.0", "2.0.0"])
    assert matched_major == ["1.0.0", "1.5.0"]


def test_requirement_hyphen_range():
    matched = _matches("1.2.3 - 2.0.0", ["1.0.0", "1.2.3", "1.5.0", "2.0.0", "2.0.1"])
    assert matched == ["1.2.3", "1.5.0", "2.0.0"]


def test_invalid_hyphen_range_upper_less_than_lower():
    with pytest.raises(ValueError):
        parseSemVerPackRequirement("2.0.0 - 1.0.0")


@pytest.mark.parametrize(
    "raw",
    [
        "^",           # missing version
        "~",           # missing version
        ">= ",         # missing version
        "<=x.y.z",     # invalid version
        "1.2.3 - ",    # bad range
        "- 1.2.3",     # bad range
    ],
)
def test_requirement_invalid_inputs(raw):
    with pytest.raises(ValueError):
        parseSemVerPackRequirement(raw)
