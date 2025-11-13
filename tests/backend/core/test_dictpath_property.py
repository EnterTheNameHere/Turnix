# tests/backend/core/test_dictpath_property.py
from __future__ import annotations
from typing import Any

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, strategies as st  # type: ignore[no-redef]

from backend.core.dictpath import getByPath, setByPath, deleteByPath


# Strategy for simple safe path segments (no raw separators)
segment_strat = st.text(
    alphabet=st.characters(blacklist_characters=[".", "/", "\\"], min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=8,
)


def _join_segments(segs: list[str]) -> str:
    return ".".join(segs)


@given(st.lists(segment_strat, min_size=1, max_size=4), st.integers())
def test_roundtrip_set_get_property(segments: list[str], value: int) -> None:
    data: dict[str, Any] = {}
    path = _join_segments(segments)

    setByPath(data, path, value, createIfMissing=True)
    assert getByPath(data, path) == value


@given(st.lists(segment_strat, min_size=1, max_size=4))
def test_delete_then_get_returns_none(segments: list[str]) -> None:
    data: dict[str, Any] = {}
    path = _join_segments(segments)

    setByPath(data, path, 1, createIfMissing=True)
    assert deleteByPath(data, path, pruneEmptyParents=True) is True
    assert getByPath(data, path) is None
