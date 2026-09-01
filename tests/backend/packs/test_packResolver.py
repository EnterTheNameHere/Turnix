import json
from pathlib import Path

import pytest

from backend.packs.runtime import PackResolver


def _manifest(root: Path, directory: str, packId: str) -> None:
    path = root / directory
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps({"packId": packId, "codeEntries": []}), encoding="utf-8")


def test_resolver_accepts_exactly_one_candidate(tmp_path):
    _manifest(tmp_path, "one", "sample")
    assert PackResolver(roots=(tmp_path,)).requireSingle("sample").packId == "sample"


def test_resolver_rejects_ambiguity(tmp_path):
    _manifest(tmp_path, "one", "sample")
    _manifest(tmp_path, "two", "sample")
    with pytest.raises(RuntimeError, match="ambiguous"):
        PackResolver(roots=(tmp_path,)).requireSingle("sample")
