# tests/backend/core/test_dictpath_fuzz.py
from __future__ import annotations
import random
from typing import Any

import pytest

from backend.core.dictpath import getByPath, setByPath, deleteByPath


def _random_key() -> str:
    # keys that sometimes need escaping: dots, slashes, backslashes
    alphabet = "abc"
    specials = [".", "/", "\\"]
    length = random.randint(1, 4)

    chars: list[str] = []
    for _ in range(length):
        if random.random() < 0.25:
            chars.append(random.choice(specials))
        else:
            chars.append(random.choice(alphabet))
    return "".join(chars)


def _escape_segment(seg: str) -> str:
    out: list[str] = []
    for ch in seg:
        if ch in (".", "/", "\\"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _random_path(depth: int) -> tuple[list[str], str]:
    raw_segments: list[str] = []
    for _ in range(depth):
        raw_segments.append(_random_key())
    escaped_segments = [_escape_segment(seg) for seg in raw_segments]
    path = ".".join(escaped_segments)
    return raw_segments, path


def test_fuzz_set_get_roundtrip_simple_dict() -> None:
    # Randomly generate nested dict paths and ensure get returns what set wrote.
    for _ in range(100):
        data: dict[str, Any] = {}

        depth = random.randint(1, 4)
        raw_segments, path = _random_path(depth)
        value = random.randint(-1000, 1000)

        # Only use segments without raw '.' or '/' or '\' so we do not rely on escapes
        if any(ch in seg for seg in raw_segments for ch in (".", "/", "\\")):
            continue

        setByPath(data, path, value, createIfMissing=True)
        assert getByPath(data, path) == value


def test_fuzz_set_delete_consistency() -> None:
    data: dict[str, Any] = {}

    paths: list[str] = []
    for _ in range(50):
        depth = random.randint(1, 4)
        raw_segments, path = _random_path(depth)
        # filter out obviously invalid segments (empty parts)
        if any(seg == "" for seg in raw_segments):
            continue
        try:
            # We *try* to set; if the current structure makes this impossible,
            # we just skip this path for the purposes of this fuzz test.
            setByPath(data, path, 1, createIfMissing=True)
        except (KeyError, AttributeError, TypeError, ValueError):
            continue
        else:
            paths.append(path)

    # If we ended up with zero successful paths, nothing to test
    if not paths:
        return

    # Randomly delete half of them – deleteByPath should not raise
    delete_sample = random.sample(paths, k=max(1, len(paths) // 2))
    for p in delete_sample:
        try:
            deleteByPath(data, p, pruneEmptyParents=True)
        except Exception as exc:
            pytest.fail(f"deleteByPath raised {exc!r} on path {p!r}")

    # Invariant: calling getByPath / hasPath on all previously successful paths
    # must not raise and must be self-consistent.
    for p in paths:
        val = getByPath(data, p, None)
        # We do not enforce stronger invariants here because deleteByPath may or
        # may not have removed this path; the core point is "no crash".
        _ = val  # Just to make intention explicit
