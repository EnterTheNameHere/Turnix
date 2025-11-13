# tests/backend/core/test_dictpath.py
from __future__ import annotations
import types
from typing import Any
from collections.abc import Mapping

import pytest

from backend.core.dictpath import getByPath, setByPath, hasPath, deleteByPath

# ----------------------------------------
# Helpers
# ----------------------------------------

class Obj:
    def __init__(self):
        self.user = types.SimpleNamespace(name="Ada")
        self.meta = {"a.b": {"c": 1}}  # key with dot (escaped access)


class AttrObj:
    def __init__(self) -> None:
        self.foo = "bar"
        self.nested = {"x": 1}


class ModelLike:
    """
    Fake Pydantic v2-style object with model_dump().
    Used to test _asMapping() behavior without depending on pydantic.
    """
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self, by_alias: bool = True, exclude_unset: bool = True) -> dict[str, Any]:
        return dict(self._data)


class ReadOnlyMapping(Mapping[str, Any]):
    """
    Immutable mapping to test write/delete errors.
    """
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


# ----------------------------------------
# getByPath / hasPath
# ----------------------------------------

def test_getByPath_simpleNestedDict() -> None:
    data = {"a": {"b": {"c": 123}}}
    assert getByPath(data, "a.b.c") == 123
    assert getByPath(data, "a.b") == {"c": 123}
    assert getByPath(data, "a.missing", "default") == "default"
    assert hasPath(data, "a.b.c") is True
    assert hasPath(data, "a.b.doesNotExist") is False


def test_getByPath_slashSeparator() -> None:
    data = {"a": {"b": {"c": 5}}}
    assert getByPath(data, "a/b/c") == 5
    # mixed separators
    assert getByPath(data, "a.b/c") == 5


def test_getByPath_escapedDotAndSlash() -> None:
    data = {"root": {"a.b": {"c/d": 42}}}
    # "a\\.b" -> "a.b"
    # "c\\/d" -> "c/d"
    assert getByPath(data, "root.a\\.b.c\\/d") == 42
    # Non-escaped variant does not match this structure
    assert getByPath(data, "root.a.b.c/d") is None


def test_getByPath_invalidPathDanglingEscape_returnsDefault() -> None:
    data = {"a": 1}
    # Trailing backslash is invalid → getByPath should treat it as "not found"
    assert getByPath(data, "a\\", "sentinel") == "sentinel"
    assert hasPath(data, "a\\") is False


def test_getByPath_attributeFallback() -> None:
    obj = AttrObj()
    assert getByPath(obj, "foo") == "bar"
    # mixing mapping and attribute
    assert getByPath(obj, "nested.x") == 1
    assert getByPath(obj, "missing.attr", "default") == "default"


def test_getByPath_modelLike_usesModelDump() -> None:
    m = ModelLike({"user": {"name": "Alice"}})
    # _asMapping() should use model_dump()
    assert getByPath(m, "user.name") == "Alice"
    assert hasPath(m, "user.name") is True


# ----------------------------------------
# setByPath
# ----------------------------------------

def test_setByPath_createIfMissing_false_keyMissing_raisesKeyError() -> None:
    data: dict[str, Any] = {}
    with pytest.raises(KeyError):
        setByPath(data, "a.b", 1, createIfMissing=False)


def test_setByPath_createIfMissing_true_buildsNestedDicts() -> None:
    data: dict[str, Any] = {}
    setByPath(data, "a.b.c", 123, createIfMissing=True)
    assert data == {"a": {"b": {"c": 123}}}
    assert getByPath(data, "a.b.c") == 123


def test_setByPath_attributeWrite() -> None:
    obj = AttrObj()
    setByPath(obj, "foo", "baz", createIfMissing=False)
    assert obj.foo == "baz"
    # nested still accessible through mapping
    setByPath(obj, "nested.x", 99, createIfMissing=False)
    assert obj.nested["x"] == 99


def test_setByPath_attributeMissing_raisesAttributeError() -> None:
    obj = AttrObj()
    with pytest.raises(AttributeError):
        setByPath(obj, "missingAttr", 123, createIfMissing=False)


def test_setByPath_onReadOnlyMapping_raisesTypeError() -> None:
    ro = ReadOnlyMapping({"a": 1})
    with pytest.raises(TypeError):
        setByPath(ro, "a", 2, createIfMissing=False)


def test_setByPath_canModifyMutableChildInsideReadOnlyMapping() -> None:
    ro = ReadOnlyMapping({"a": {"b": 1}})
    # This should NOT raise
    setByPath(ro, "a.b", 2, createIfMissing=False)
    # Underlying child object is mutated
    assert ro["a"]["b"] == 2


def test_setByPath_invalidPath_raisesValueError() -> None:
    data: dict[str, Any] = {}
    with pytest.raises(ValueError):
        setByPath(data, "a..b", 1)
    with pytest.raises(ValueError):
        setByPath(data, ".a.b", 1)
    with pytest.raises(ValueError):
        setByPath(data, "a.b.", 1)
    with pytest.raises(ValueError):
        setByPath(data, "a\\", 1)


# ----------------------------------------
# deleteByPath
# ----------------------------------------

def test_deleteByPath_simpleDict_prunesByDefault() -> None:
    data: dict[str, Any] = {
        "a": {"b": {"c": 123}},
        "x": 1,
    }
    removed = deleteByPath(data, "a.b.c", pruneEmptyParents=True)
    assert removed is True
    # "b" should be pruned; root "a" remains as an empty dict
    assert data == {"a": {}, "x": 1}


def test_deleteByPath_noPrune_keepsEmptyParents() -> None:
    data: dict[str, Any] = {"a": {"b": {"c": 123}}}
    removed = deleteByPath(data, "a.b.c", pruneEmptyParents=False)
    assert removed is True
    assert data == {"a": {"b": {}}}


def test_deleteByPath_missingPath_returnsFalse() -> None:
    data: dict[str, Any] = {"a": {"b": {"c": 1}}}
    removed = deleteByPath(data, "a.b.x", pruneEmptyParents=True)
    assert removed is False
    assert data == {"a": {"b": {"c": 1}}}


def test_deleteByPath_onReadOnlyMapping_raisesTypeError() -> None:
    ro = ReadOnlyMapping({"a": {"b": 1}})
    # Deleting top-level key 'a' should try to modify the read-only mapping itself
    with pytest.raises(TypeError):
        deleteByPath(ro, "a")


def test_deleteByPath_canModifyMutableChildInsideReadOnlyMapping() -> None:
    ro = ReadOnlyMapping({"a": {"b": 1}})
    # This should NOT raise: mutable child is allowed to change
    assert deleteByPath(ro, "a.b", pruneEmptyParents=True) is True
    assert ro["a"] == {}


def test_deleteByPath_attributeDeletion() -> None:
    obj = AttrObj()
    assert deleteByPath(obj, "foo") is True
    assert not hasattr(obj, "foo")
    # deleting non-existing attribute returns False, not error
    assert deleteByPath(obj, "foo") is False


def test_get_valid_and_invalid_paths():
    o = {"a": {"b": {"c": 3}}}
    assert getByPath(o, "a.b.c") == 3
    assert getByPath(o, "a..b", 42) == 42       # empty segment → default
    assert getByPath(o, r"a.b\\", 99) == 99     # dangling escape → default
    assert hasPath(o, "a.b.c") is True
    assert hasPath(o, "a.b.") is False


def test_escaped_separators():
    o = Obj()
    # Access key literally "a.b"
    assert getByPath(o, r"meta.a\.b.c") == 1


def test_attr_then_mapping_fallback():
    o = Obj()
    assert getByPath(o, "user.name") == "Ada"
    assert hasPath(o, "user.name") is True


def test_set_create_if_missing_mapping():
    o = {}
    setByPath(o, "a.b.c", 7, createIfMissing=True)
    assert o == {"a": {"b": {"c": 7}}}


def test_set_no_attr_autocreate():
    o = Obj()
    try:
        setByPath(o, "user.age", 42)  # age does not exist → error
    except AttributeError:
        pass
    else:
        assert False, "should not auto-create attributes"


def test_delete_and_prune_mapping_parents():
    o = {"a": {"b": {"c": 1, "d": 2}}}
    assert deleteByPath(o, "a.b.c", pruneEmptyParents=True) is True
    assert o == {"a": {"b": {"d": 2}}}
    assert deleteByPath(o, "a.b.d", pruneEmptyParents=True) is True
    # b becomes empty → pruned from a
    assert o == {"a": {}}


def test_delete_attr_and_no_prune_through_attr():
    class X:
        def __init__(self):
            self.child = types.SimpleNamespace(leaf=1)
    x = X()
    assert deleteByPath(x, "child.leaf", pruneEmptyParents=True) is True
    # attribute parents are not pruned (by design)
    assert hasattr(x, "child")


# ----------------------------------------
# Exotic path parsing & escape behavior
# ----------------------------------------

def test_escaped_backslash_in_key() -> None:
    # Key literally "key\name"
    data = {"root": {"key\\name": {"sub": 42}}}
    # "key\\name" in a raw string → key\name in the parsed segment
    assert getByPath(data, r"root.key\\name.sub") == 42


def test_escaped_leading_dot_segment() -> None:
    # Key literally ".a"
    data = {".a": {"b": 1}}
    # "\.a.b" → [".a", "b"]
    assert getByPath(data, r"\.a.b") == 1


def test_escaped_trailing_dot_segment() -> None:
    # Key literally "b."
    data = {"a": {"b.": 2}}
    # "a.b\." → ["a", "b."]
    assert getByPath(data, r"a.b\.") == 2


def test_escaped_slash_segment() -> None:
    # Key literally "a/b"
    data = {"a/b": {"c": 3}}
    # "a\/b.c" → ["a/b", "c"]
    assert getByPath(data, r"a\/b.c") == 3


def test_multiple_escaped_separators_in_one_key() -> None:
    # Key literally "a.b/c"
    data = {"root": {"a.b/c": {"x": 7}}}
    # "a\.b\/c" → ["a.b/c"]
    assert getByPath(data, r"root.a\.b\/c.x") == 7


# ----------------------------------------
# Mixed mapping + attribute hops
# ----------------------------------------

class UserProfile:
    def __init__(self) -> None:
        self.settings = {"volume": 10}


class UserContainer:
    def __init__(self) -> None:
        self.user = UserProfile()


def test_attr_to_mapping_to_attr_chain() -> None:
    obj = UserContainer()
    # user (attr) → settings (attr: dict) → volume (mapping key)
    assert getByPath(obj, "user.settings.volume") == 10

    setByPath(obj, "user.settings.volume", 20, createIfMissing=False)
    assert obj.user.settings["volume"] == 20
    assert getByPath(obj, "user.settings.volume") == 20


class Middle:
    def __init__(self) -> None:
        self.y = {"z": 7}


def test_mapping_to_attr_to_mapping_chain() -> None:
    obj: dict[str, Any] = {"x": Middle()}

    # x (mapping) → y (attr: dict) → z (mapping key)
    assert getByPath(obj, "x.y.z") == 7

    setByPath(obj, "x.y.z", 99, createIfMissing=False)
    assert obj["x"].y["z"] == 99
    assert getByPath(obj, "x.y.z") == 99


# ----------------------------------------
# Deep paths + prune behavior
# ----------------------------------------

def test_deleteByPath_deep_nesting_prunes_inner_not_root() -> None:
    o: dict[str, Any] = {"root": {"a": {"b": {"c": {"d": {"e": 1}}}}}}
    removed = deleteByPath(o, "root.a.b.c.d.e", pruneEmptyParents=True)
    assert removed is True
    # Only "root" is preserved; all inner empty dicts under that branch are pruned
    assert o == {"root": {}}


# ----------------------------------------
# Immutable vs mutable nesting combos
# ----------------------------------------

def test_setByPath_mutable_parent_immutable_child_raisesTypeError() -> None:
    ro_child = ReadOnlyMapping({"x": 1})
    data: dict[str, Any] = {"root": ro_child}
    # Last parent is read-only mapping → cannot set key "x"
    with pytest.raises(TypeError):
        setByPath(data, "root.x", 2, createIfMissing=False)


def test_deleteByPath_mutable_parent_immutable_child_raisesTypeError() -> None:
    ro_child = ReadOnlyMapping({"x": 1})
    data: dict[str, Any] = {"root": ro_child}
    # Last parent is read-only mapping → cannot delete key "x"
    with pytest.raises(TypeError):
        deleteByPath(data, "root.x")


class NestedReadOnly:
    """
    Immutable parent with nested immutable child:
      ro = ReadOnlyMapping({"x": {"y": ReadOnlyMapping({"z": 9})}})
    We should not be able to modify the inner read-only mapping.
    """


def test_setByPath_immutable_grandchild_raisesTypeError() -> None:
    ro_inner = ReadOnlyMapping({"z": 9})
    ro_outer = ReadOnlyMapping({"y": ro_inner})
    root = {"x": ro_outer}

    with pytest.raises(TypeError):
        setByPath(root, "x.y.z", 10, createIfMissing=False)

    with pytest.raises(TypeError):
        deleteByPath(root, "x.y.z")


# ----------------------------------------
# Pydantic-like error behavior
# ----------------------------------------

class BadModel:
    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("broken dump")


def test_getByPath_modelLike_raises_in_model_dump_falls_back_to_default() -> None:
    m = BadModel()
    # _asMapping() will try model_dump, catch error, and treat as non-mapping
    # Then attribute resolution fails too → default
    assert getByPath(m, "user.name", "default") == "default"
    assert hasPath(m, "user.name") is False


# ----------------------------------------
# Dynamic attribute objects
# ----------------------------------------

class Dynamic:
    """
    Object whose attributes are created on the fly.
    We want to see how attribute + mapping resolution works.
    """
    def __init__(self) -> None:
        self._children: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        if name not in self._children:
            # Each child is a SimpleNamespace-like container
            self._children[name] = types.SimpleNamespace()
        return self._children[name]


def test_dynamic_attr_object_get_and_set() -> None:
    d = Dynamic()

    # Create foo and its bar attribute via normal Python attribute access
    foo = getattr(d, "foo")  # triggers __getattr__, creates SimpleNamespace
    foo.bar = 1

    # Sanity check: getByPath sees it
    assert getByPath(d, "foo.bar") == 1
    assert hasPath(d, "foo.bar") is True

    # Now setByPath should be allowed to modify an existing attribute
    setByPath(d, "foo.bar", 99, createIfMissing=False)

    # Underlying object updated
    assert getattr(d.foo, "bar") == 99
    assert getByPath(d, "foo.bar") == 99


# ----------------------------------------
# Mixed escaped path segments + attribute traversal
# ----------------------------------------

def test_mixed_escaped_segments_with_attr_chain() -> None:
    # meta["weird.key"] is a SimpleNamespace(value=7)
    meta = {"weird.key": types.SimpleNamespace(value=7)}
    obj = types.SimpleNamespace(meta=meta)

    # "meta.weird\.key.value" → ["meta", "weird.key", "value"]
    assert getByPath(obj, r"meta.weird\.key.value") == 7

    setByPath(obj, r"meta.weird\.key.value", 8, createIfMissing=False)
    assert obj.meta["weird.key"].value == 8
    assert getByPath(obj, r"meta.weird\.key.value") == 8


# ----------------------------------------
# Root pruning stability on repeated deletes
# ----------------------------------------

def test_deleteByPath_repeated_deletes_do_not_prune_root() -> None:
    o: dict[str, Any] = {"a": {"b": 1}}
    # First delete leaf
    assert deleteByPath(o, "a.b", pruneEmptyParents=True) is True
    # Now 'a' is an empty dict (leaf pruned, root key kept)
    assert o == {"a": {}}

    # Second delete removes the top-level key 'a'
    assert deleteByPath(o, "a", pruneEmptyParents=True) is True
    # Root container still exists, but has no keys
    assert o == {}
