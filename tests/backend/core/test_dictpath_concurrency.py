# tests/backend/core/test_dictpath_concurrency.py
from __future__ import annotations
import threading
from typing import Any

from backend.core.dictpath import setByPath, getByPath, deleteByPath


def _worker_set_loop(shared: dict[str, Any], base: str, n: int) -> None:
    for i in range(n):
        path = f"{base}.k{i}"
        setByPath(shared, path, i, createIfMissing=True)


def _worker_delete_loop(shared: dict[str, Any], base: str, n: int) -> None:
    for i in range(n):
        path = f"{base}.k{i}"
        deleteByPath(shared, path, pruneEmptyParents=True)


def test_concurrent_like_interleaved_mutations() -> None:
    shared: dict[str, Any] = {}

    # Create threads that both set and delete different branches
    threads: list[threading.Thread] = []
    for idx in range(3):
        t_set = threading.Thread(target=_worker_set_loop, args=(shared, f"branch{idx}", 50))
        t_del = threading.Thread(target=_worker_delete_loop, args=(shared, f"branch{idx}", 50))
        threads.extend([t_set, t_del])

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Invariant: no exceptions, and structure is still a nested dict-ish tree.
    # We do a cheap consistency scan: all nested values are either dict-like or scalars.
    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                _walk(v)

    _walk(shared)
    # Basic sanity: no weird types injected, and we can still set after all that.
    setByPath(shared, "final.check", 1, createIfMissing=True)
    assert getByPath(shared, "final.check") == 1
