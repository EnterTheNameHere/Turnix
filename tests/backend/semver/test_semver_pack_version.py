import pytest

from backend.semver.semver import parseSemverPackVersion, SemverPackVersion


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1",        (1, 0, 0, (), ())),
        ("1.2",      (1, 2, 0, (), ())),
        ("1.2.3",    (1, 2, 3, (), ())),
        ("0.1",      (0, 1, 0, (), ())),
        ("0.0.1",    (0, 0, 1, (), ())),
        ("v1",       (1, 0, 0, (), ())),
        ("v1.2.3",   (1, 2, 3, (), ())),
        ("1.2.3-alpha",           (1, 2, 3, ("alpha",), ())),
        ("1.2.3-alpha.1",         (1, 2, 3, ("alpha", "1"), ())),
        ("1.2.3+build.1",         (1, 2, 3, (), ("build", "1"))),
        ("1.2.3-alpha+exp.sha",   (1, 2, 3, ("alpha",), ("exp", "sha"))),
    ],
)
def test_parseSemverPackVersion_valid(raw, expected):
    v = parseSemverPackVersion(raw)
    major, minor, patch, pre, build = expected
    assert (v.major, v.minor, v.patch, v.prerelease, v.build) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        ".1",
        "1.",
        "1..2",
        "1.2.3.4",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3-",
        "1.2.3+",
        "v",
        "vv1.2.3",
    ],
)
def test_parseSemverPackVersion_invalid(raw):
    with pytest.raises(ValueError):
        parseSemverPackVersion(raw)


@pytest.mark.parametrize(
    "a, b",
    [
        ("1.0.0-alpha", "1.0.0-alpha.1"),
        ("1.0.0-alpha.1", "1.0.0-alpha.beta"),
        ("1.0.0-alpha.beta", "1.0.0-beta"),
        ("1.0.0-beta", "1.0.0-beta.2"),
        ("1.0.0-beta.2", "1.0.0-beta.11"),
        ("1.0.0-beta.11", "1.0.0-rc.1"),
        ("1.0.0-rc.1", "1.0.0"),
    ],
)
def test_semver_prerelease_order(a, b):
    va = parseSemverPackVersion(a)
    vb = parseSemverPackVersion(b)
    assert va < vb


def test_build_metadata_ignored_in_comparison():
    a = parseSemverPackVersion("1.0.0+build.1")
    b = parseSemverPackVersion("1.0.0+build.2")
    c = parseSemverPackVersion("1.0.0")

    assert a == b
    assert a == c
    assert not (a < b)
    assert not (b < a)
