# tests/test_config_providers.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import json5
import pytest

from backend.config.providers import (
    OverrideProvider,
    DictProvider,
    DefaultsProvider,
    FileProvider,
    ViewProvider,
)
from backend.core.dictpath import getByPath


# ----------------------------
# Helpers
# ----------------------------

class DummyStore:
    """
    Minimal stand-in for ConfigStore for testing ViewProvider.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, key: str) -> Any | None:
        return getByPath(self._data, key, None)

    def snapshot(self) -> dict[str, Any]:
        # ViewProvider expects snapshot()["values"]
        return {"values": dict(self._data)}


# ----------------------------
# OverrideProvider tests
# ----------------------------

def test_runtimeProvider_setAndGet_pathCreatesNested() -> None:
    provider = OverrideProvider()

    provider.set("graphics.video.fullscreen", True)
    provider.set("graphics.video.vsync", False)

    assert provider.get("graphics.video.fullscreen") is True
    assert provider.get("graphics.video.vsync") is False
    # Non-existing key
    assert provider.get("graphics.audio.muted") is None

    # Underlying data is nested
    data = provider.to_dict()
    assert data == {
        "graphics": {
            "video": {
                "fullscreen": True,
                "vsync": False,
            },
        },
    }


def test_runtimeProvider_delete_prunesEmptyParents() -> None:
    provider = OverrideProvider()
    provider.set("a.b.c", 123)
    provider.set("a.x", "keep")

    # Delete nested leaf
    provider.set("a.b.c", None)

    data = provider.to_dict()
    # "b" should have been pruned as empty; "a" stays because it has "x"
    assert data == {"a": {"x": "keep"}}
    assert provider.get("a.b.c") is None
    assert provider.get("a.x") == "keep"


# ----------------------------
# DictProvider tests
# ----------------------------

def test_dictProvider_readOnly_getByPath() -> None:
    base = {"protocol": {"ackWaitMs": 250, "retryCount": 3}}
    provider = DictProvider(data=base)

    assert provider.get("protocol.ackWaitMs") == 250
    assert provider.get("protocol.retryCount") == 3
    assert provider.get("protocol.missing") is None

    # Underlying mapping should not be mutated by to_dict()
    snapshot = provider.to_dict()
    snapshot["protocol"]["ackWaitMs"] = 999
    assert base["protocol"]["ackWaitMs"] == 250


def test_dictProvider_isReadOnly() -> None:
    provider = DictProvider(data={"a": 1})
    with pytest.raises(RuntimeError):
        provider.set("a", 2)


# ----------------------------
# DefaultsProvider tests
# ----------------------------

def test_defaultsProvider_dataMapping_ok() -> None:
    defaults = {"protocol": {"ackWaitMs": 250}}
    provider = DefaultsProvider(data=defaults)

    assert provider.get("protocol.ackWaitMs") == 250
    # Ensure deep copy in to_dict does not mutate original
    out = provider.to_dict()
    out["protocol"]["ackWaitMs"] = 999
    assert defaults["protocol"]["ackWaitMs"] == 250


def test_defaultsProvider_dataNonMapping_raises() -> None:
    with pytest.raises(TypeError):
        DefaultsProvider(data=123)  # type: ignore[arg-type]


def test_defaultsProvider_path_missing_strictFalse(tmp_path: Path) -> None:
    cfgPath = tmp_path / "does_not_exist.json5"
    provider = DefaultsProvider(path=cfgPath, strict=False)

    assert provider.get("anything") is None
    assert provider.to_dict() == {}


def test_defaultsProvider_path_missing_strictTrue(tmp_path: Path) -> None:
    cfgPath = tmp_path / "does_not_exist.json5"
    with pytest.raises(FileNotFoundError):
        DefaultsProvider(path=cfgPath, strict=True)


def test_defaultsProvider_path_parsesObject(tmp_path: Path) -> None:
    cfgPath = tmp_path / "defaults.json5"
    data = {"graphics": {"fullscreen": True, "width": 1920}}
    cfgPath.write_text(json5.dumps(data), encoding="utf-8")

    provider = DefaultsProvider(path=cfgPath)

    assert provider.get("graphics.fullscreen") is True
    assert provider.get("graphics.width") == 1920
    assert provider.to_dict() == data


def test_defaultsProvider_path_nonMapping_raises(tmp_path: Path) -> None:
    cfgPath = tmp_path / "defaults_scalar.json5"
    cfgPath.write_text(json5.dumps(42), encoding="utf-8")

    with pytest.raises(TypeError):
        DefaultsProvider(path=cfgPath)


# ----------------------------
# FileProvider tests
# ----------------------------

def test_fileProvider_missing_startsEmpty(tmp_path: Path) -> None:
    cfgPath = tmp_path / "user_config.json5"
    provider = FileProvider(cfgPath)

    # No file yet, should behave as empty
    assert provider.get("foo.bar") is None
    assert provider.to_dict() == {}


def test_fileProvider_setPath_saveAndReload(tmp_path: Path) -> None:
    cfgPath = tmp_path / "user_config.json5"
    provider = FileProvider(cfgPath)

    provider.set("debug.tracingEnabled", True)
    provider.set("graphics.video.width", 1920)
    provider.set("graphics.video.height", 1080)
    provider.save()

    # Reload into a fresh provider
    provider2 = FileProvider(cfgPath)
    assert provider2.get("debug.tracingEnabled") is True
    assert provider2.get("graphics.video.width") == 1920
    assert provider2.get("graphics.video.height") == 1080

    # Underlying file should contain an object
    text = cfgPath.read_text(encoding="utf-8")
    parsed = json5.loads(text)
    assert isinstance(parsed, dict)
    assert parsed["graphics"]["video"]["width"] == 1920


def test_fileProvider_deleteViaNone_prunes(tmp_path: Path) -> None:
    cfgPath = tmp_path / "user_config.json5"
    provider = FileProvider(cfgPath)

    provider.set("a.b.c", 123)
    provider.set("a.x", "keep")
    provider.save()

    provider.set("a.b.c", None)
    provider.save()

    provider2 = FileProvider(cfgPath)
    data = provider2.to_dict()
    assert data == {"a": {"x": "keep"}}
    assert provider2.get("a.b.c") is None
    assert provider2.get("a.x") == "keep"


def test_fileProvider_readOnly_blocksWrites(tmp_path: Path) -> None:
    cfgPath = tmp_path / "ro.json5"
    cfgPath.write_text(json5.dumps({"a": 1}), encoding="utf-8")

    provider = FileProvider(cfgPath, readOnly=True)

    with pytest.raises(RuntimeError):
        provider.set("a", 2)

    with pytest.raises(RuntimeError):
        provider.save()


# ----------------------------
# ViewProvider tests
# ----------------------------

def test_viewProvider_delegatesGetAndSnapshot() -> None:
    backingData = {
        "global": {"lang": "en", "volume": 0.7},
        "debug": {"enabled": False},
    }
    store = DummyStore(backingData)
    provider = ViewProvider(store)

    assert provider.get("global.lang") == "en"
    assert provider.get("debug.enabled") is False
    assert provider.get("missing.key") is None

    snap = provider.to_dict()
    assert snap == backingData

    # Mutating the snapshot must not mutate the store
    snap["global"]["lang"] = "cz"
    assert store.get("global.lang") == "en"


def test_viewProvider_isReadOnly() -> None:
    store = DummyStore({"a": 1})
    provider = ViewProvider(store)

    with pytest.raises(RuntimeError):
        provider.set("a", 2)


def test_runtimeProvider_escapedDotInKey() -> None:
    provider = OverrideProvider()

    # "a\\.b.c" → ["a.b", "c"]
    provider.set("root.a\\.b.c", 123)

    data = provider.to_dict()
    assert data == {"root": {"a.b": {"c": 123}}}

    assert provider.get("root.a\\.b.c") == 123
    # Non-escaped variant should not resolve, different key structure
    assert provider.get("root.a.b.c") is None


def test_runtimeProvider_invalidPath_get_returnsNone() -> None:
    provider = OverrideProvider()
    provider.set("a.b", 1)

    # Trailing backslash → invalid path; getByPath treats it as "not found"
    assert provider.get("a\\") is None


def test_runtimeProvider_invalidPath_set_raisesValueError() -> None:
    provider = OverrideProvider()

    # Trailing backslash should propagate ValueError from setByPath
    with pytest.raises(ValueError):
        provider.set("a\\", 123)


def test_defaultsProvider_path_isDirectory_raises(tmp_path: Path) -> None:
    dir_path = tmp_path / "cfgdir"
    dir_path.mkdir()

    # Path exists but is not a file → should raise
    with pytest.raises(FileNotFoundError):
        DefaultsProvider(path=dir_path)


def test_fileProvider_invalidJson_logsAndStartsEmpty(tmp_path: Path) -> None:
    cfgPath = tmp_path / "bad.json5"
    # Intentionally invalid JSON5 content
    cfgPath.write_text("{ this is: not valid json5 ...", encoding="utf-8")

    provider = FileProvider(cfgPath)

    # Parse failure should not raise, but start as empty dict
    assert provider.to_dict() == {}
    assert provider.get("anything") is None


def test_fileProvider_invalidPathOnDeleteDoesNotRaise(tmp_path: Path) -> None:
    cfgPath = tmp_path / "user_config.json5"
    provider = FileProvider(cfgPath)

    # Deleting a missing path via value=None should be a no-op, not an error
    provider.set("nonexistent.path", None)

    assert provider.to_dict() == {}


def test_viewProvider_save_isNoop() -> None:
    backingData = {"a": 1}
    store = DummyStore(backingData)
    provider = ViewProvider(store)

    # Should not raise, and should not mutate the store
    provider.save()
    assert store.get("a") == 1

