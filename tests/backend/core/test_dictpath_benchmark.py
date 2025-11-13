# tests/backend/core/test_dictpath_benchmark.py
from __future__ import annotations
from typing import Any

import pytest

pytest.importorskip("pytest_benchmark")  # auto-skip if plugin not installed

from backend.core.dictpath import getByPath, setByPath, deleteByPath  # noqa: E402


@pytest.mark.benchmark(group="dictpath_get")
def test_bench_getByPath_deep(benchmark) -> None:
    data: dict[str, Any] = {}
    # 10-level path
    path = "a.b.c.d.e.f.g.h.i.j"
    setByPath(data, path, 123, createIfMissing=True)

    def _run() -> Any:
        return getByPath(data, path)

    result = benchmark(_run)
    assert result == 123


@pytest.mark.benchmark(group="dictpath_set")
def test_bench_setByPath_deep(benchmark) -> None:
    data: dict[str, Any] = {}
    path = "root.a.b.c.d.e.f.g.h.i"

    def _run() -> None:
        setByPath(data, path, 42, createIfMissing=True)

    benchmark(_run)
    # basic sanity
    assert getByPath(data, path) == 42


@pytest.mark.benchmark(group="dictpath_delete")
def test_bench_deleteByPath_deep(benchmark) -> None:
    data: dict[str, Any] = {}
    path = "root.a.b.c.d.e.f.g.h.i"
    setByPath(data, path, 1, createIfMissing=True)

    def _run() -> None:
        deleteByPath(data, path, pruneEmptyParents=True)
        # re-create for next run
        setByPath(data, path, 1, createIfMissing=True)

    benchmark(_run)
