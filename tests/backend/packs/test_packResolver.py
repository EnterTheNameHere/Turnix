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


def test_resolver_uses_one_discovery_snapshot(tmp_path):
    _manifest(tmp_path, "one", "first")
    _manifest(tmp_path, "two", "second")
    resolver = PackResolver(roots=(tmp_path,))

    assert resolver.requireSingle("first").packId == "first"

    # The resolver models one static content-root snapshot. A malformed file
    # appearing after discovery must not make later lookups re-scan the roots.
    late = tmp_path / "late"
    late.mkdir()
    (late / "manifest.json").write_text("not json", encoding="utf-8")

    assert resolver.requireSingle("second").packId == "second"


def test_resolver_reports_malformed_manifest_during_discovery(tmp_path):
    malformed = tmp_path / "bad"
    malformed.mkdir()
    (malformed / "manifest.json").write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Could not read Pack manifest"):
        PackResolver(roots=(tmp_path,)).requireSingle("anything")


def test_resolver_rejects_invalid_requested_identity(tmp_path):
    with pytest.raises(ValueError, match="non-empty exact string"):
        PackResolver(roots=(tmp_path,)).requireSingle("")
