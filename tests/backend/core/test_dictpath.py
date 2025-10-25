# tests/core/test_dictpath.py
import types
from backend.core.dictpath import getByPath, setByPath, hasPath, deleteByPath

class Obj:
    def __init__(self):
        self.user = types.SimpleNamespace(name="Ada")
        self.meta = {"a.b": {"c": 1}}  # key with dot (escaped access)

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
