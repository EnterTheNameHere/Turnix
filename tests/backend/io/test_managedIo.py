# file: tests/backend/io/test_managedIo.py ; version: 2
from __future__ import annotations

import hashlib

import pytest

from backend.io import IoPathError, ManagedIo


def test_metadata_observation_represents_missing_file(tmp_path) -> None:
    path = tmp_path / "source.txt"

    observation = ManagedIo().observeFile(path)

    assert observation.snapshot() == {
        "path": str(path.resolve()),
        "state": "missing",
        "sizeBytes": None,
        "modifiedTimeNs": None,
        "contentSha256": None,
    }


def test_metadata_observation_detects_file_creation_and_change(tmp_path) -> None:
    path = tmp_path / "source.txt"
    io = ManagedIo()

    missing = io.observeFile(path)
    path.write_text("first", encoding="utf-8")
    first = io.observeFile(path)
    path.write_text("second value", encoding="utf-8")
    second = io.observeFile(path)

    assert missing.state == "missing"
    assert first.state == "file"
    assert first.sizeBytes == 5
    assert first.contentSha256 is None

    assert second.state == "file"
    assert second.sizeBytes == 12
    assert second.contentSha256 is None
    assert second != first


def test_strong_observation_provides_stable_content_identity(tmp_path) -> None:
    path = tmp_path / "source.txt"
    path.write_text("same bytes", encoding="utf-8")
    io = ManagedIo()

    first = io.observeFile(path, contentHash=True)
    second = io.observeFile(path, contentHash=True)

    expected = hashlib.sha256(b"same bytes").hexdigest()
    assert first.contentSha256 == expected
    assert second.contentSha256 == expected
    assert first == second


def test_strong_observation_detects_content_change(tmp_path) -> None:
    path = tmp_path / "source.txt"
    io = ManagedIo()

    path.write_text("alpha", encoding="utf-8")
    first = io.observeFile(path, contentHash=True)

    path.write_text("omega", encoding="utf-8")
    second = io.observeFile(path, contentHash=True)

    assert first.sizeBytes == second.sizeBytes
    assert first.contentSha256 != second.contentSha256
    assert first != second


def test_hashing_non_file_source_is_rejected(tmp_path) -> None:
    with pytest.raises(IoPathError, match="regular file"):
        ManagedIo().observeFile(tmp_path, contentHash=True)


def test_staged_write_is_invisible_until_commit(tmp_path) -> None:
    path = tmp_path / "result.json"
    io = ManagedIo()
    transaction = io.openTransaction()

    transaction.writeJsonAtomic(path, {"value": 1})

    assert path.exists() is False

    transaction.commit()

    assert path.read_text(encoding="utf-8") == '{\n  "value": 1\n}\n'


def test_staged_write_abort_discards_output(tmp_path) -> None:
    path = tmp_path / "result.txt"
    io = ManagedIo()
    transaction = io.openTransaction()

    transaction.writeTextAtomic(path, "staged")
    transaction.abort()

    assert path.exists() is False


def test_staged_write_batch_replaces_multiple_destinations_on_commit(tmp_path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("old-first", encoding="utf-8")
    second.write_text("old-second", encoding="utf-8")

    transaction = ManagedIo().openTransaction()
    transaction.writeTextAtomic(first, "new-first")
    transaction.writeTextAtomic(second, "new-second")
    transaction.commit()

    assert first.read_text(encoding="utf-8") == "new-first"
    assert second.read_text(encoding="utf-8") == "new-second"


def test_resolved_staged_io_transaction_rejects_reuse(tmp_path) -> None:
    transaction = ManagedIo().openTransaction()
    transaction.writeTextAtomic(tmp_path / "value.txt", "value")
    transaction.commit()

    with pytest.raises(RuntimeError, match="already committed"):
        transaction.writeTextAtomic(tmp_path / "other.txt", "other")
