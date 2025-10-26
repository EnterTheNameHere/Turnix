# tests/test_schema_registry.py
import copy
import pytest
import threading
from typing import cast

import fastjsonschema

from backend.core.schema_registry import (
    SchemaRegistry,
    SchemaDoc,
    Descriptor,
    ValidationError,
    JSONSchemaRoot,
    JSONValue
)


def d(ns: str, name: str, ver: str = "1.0.0", prio: int = 0) -> Descriptor:
    return Descriptor(namespace=ns, name=name, version=ver, priority=prio)


def test_add_and_get_schema_roundtrip_and_immutability():
    reg = SchemaRegistry()
    root = {
        "$id": "id://RootA",
        "type": "object",
        "properties": {"x": {"type": "integer"}},
    }
    doc = SchemaDoc(desc=d("mod", "A"), schema=copy.deepcopy(root))
    reg.addSchema(doc)

    # getSchema returns a copy
    got = reg.getSchema("mod", "A")
    assert got == root and got is not root

    # getById returns a copy for dict-schemas
    byid = reg.getById("id://RootA")
    assert byid == root and byid is not root

    # Mutating original should not affect registry
    root["properties"]["x"]["minimum"] = 5
    again = cast(dict, reg.getById("id://RootA"))
    assert "minimum" not in again["properties"]["x"]


def test_boolean_schema_true_validates_everything_false_rejects_all():
    reg = SchemaRegistry()

    reg.addSchema(SchemaDoc(desc=d("mod", "AllowAll"), schema=True))
    reg.addSchema(SchemaDoc(desc=d("mod", "DenyAll"), schema=False))

    # True accepts anything
    reg.validate(namespace="mod", name="AllowAll", instance=None)
    reg.validate(namespace="mod", name="AllowAll", instance={"any": ["thing"]})

    # False rejects everything
    with pytest.raises(ValidationError):
        reg.validate(namespace="mod", name="DenyAll", instance={})
    with pytest.raises(ValidationError):
        reg.validate(namespace="mod", name="DenyAll", instance=123)


def test_local_pointer_and_local_anchor_resolution():
    reg = SchemaRegistry()
    schema = {
        "$id": "id://LocalDoc",
        "$defs": {
            "S": {"type": "string"},
            "WithAnchor": {"$anchor": "A1", "type": "integer"},
        },
        "allOf": [
            {"$ref": "#/$defs/S"},   # JSON pointer
            {"$ref": "#A1"},         # local anchor
        ],
    }
    reg.addSchema(SchemaDoc(desc=d("game", "Local"), schema=schema))
    v = reg.getValidator("game", "Local")

    # Must fail because it can't be both string and integer
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("hello")


def test_external_id_and_pointer_resolution():
    reg = SchemaRegistry()

    # External library with an id and a pointer target
    lib = {
        "$id": "id://Lib",
        "$defs": {
            "Int": {"type": "integer"},
            "Str": {"type": "string"},
        },
        # also an anchored node
        "NumberNode": {"$anchor": "NumAnchor", "type": "number"},
    }

    root = {
        "$id": "id://UsesLib",
        "type": "object",
        "properties": {
            "a": {"$ref": "id://Lib#/$defs/Int"},    # pointer into id://Lib
            "b": {"$ref": "id://Lib#/$defs/Str"},
            "c": {"$ref": "id://Lib#NumAnchor"},     # external anchor
        },
        "required": ["a", "b", "c"],
        "additionalProperties": False,
    }

    reg.addSchema(SchemaDoc(desc=d("mod", "UsesLib"), schema=root, refs={"id://Lib": lib}))

    v = reg.getValidator("mod", "UsesLib")
    v({"a": 1, "b": "ok", "c": 3.14})

    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v({"a": "nope", "b": "ok", "c": 3.14})

    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v({"a": 1, "b": "ok", "c": "NaN"})


def test_find_unresolved_refs_reports_missing_absolute_ids_only():
    reg = SchemaRegistry()

    root = {
        "$id": "id://Main",
        "type": "object",
        "properties": {
            "x": {"$ref": "id://Missing"},           # absolute id, missing
            "y": {"$ref": "#/$defs/Here"},           # local pointer (ignored by unresolved)
        },
        "$defs": {"Here": {"type": "string"}},
    }

    reg.addSchema(SchemaDoc(desc=d("global", "Main"), schema=root))
    unresolved = reg.findUnresolvedRefs()
    assert unresolved == ["id://Missing"]


def test_id_collision_policy_supersede_ok_cross_doc_raises():
    reg = SchemaRegistry()

    # Base doc
    doc1 = SchemaDoc(
        desc=d("asset", "S", "1.0.0"),
        schema={"$id": "id://Same", "type": "number"},
    )
    reg.addSchema(doc1)

    # Same (ns,name), higher version, identical content -> OK
    doc2_same = SchemaDoc(
        desc=d("asset", "S", "1.1.0"),
        schema={"$id": "id://Same", "type": "number"},
    )
    reg.addSchema(doc2_same)  # OK

    # Same (ns,name), higher version, DIFFERENT content -> now allowed (supersede)
    doc2_diff = SchemaDoc(
        desc=d("asset", "S", "2.0.0"),
        schema={"$id": "id://Same", "type": "string"},
    )
    reg.addSchema(doc2_diff)  # OK under current policy

    # Different (ns,name), reusing the same $id with different content -> must raise
    other_conflict = SchemaDoc(
        desc=d("asset", "T", "1.0.0"),  # different name
        schema={"$id": "id://Same", "type": "boolean"},
    )
    import pytest
    with pytest.raises(ValueError):
        reg.addSchema(other_conflict)

    # And ensure the active doc for (asset:S) is the superseded one
    got = reg.getById("id://Same")
    assert isinstance(got, dict) and got.get("type") == "string"


def test_anchor_collision_same_node_ok_different_node_raises():
    reg = SchemaRegistry()

    schema1 = {
        "$id": "id://Anchored",
        "Foo": {"$anchor": "A", "type": "string"},
    }
    schema2_same = {
        "$id": "id://Anchored",
        "Foo": {"$anchor": "A", "type": "string"},  # exact same node
    }
    schema2_diff = {
        "$id": "id://Anchored",
        "Foo": {"$anchor": "A", "type": "integer"},  # different node for same anchor
    }

    reg.addSchema(SchemaDoc(desc=d("modpack", "P1"), schema=schema1))
    reg.addSchema(SchemaDoc(desc=d("modpack", "P2"), schema=schema2_same))  # ok

    with pytest.raises(ValueError):
        reg.addSchema(SchemaDoc(desc=d("modpack", "P3"), schema=schema2_diff))


def test_remove_schema_and_purge_ids():
    reg = SchemaRegistry()

    ext = {"$id": "id://Ext", "$defs": {"X": {"type": "integer"}}}
    root = {"$id": "id://Root", "allOf": [{"$ref": "id://Ext#/$defs/X"}]}

    reg.addSchema(SchemaDoc(desc=d("runtime", "R"), schema=root, refs={"id://Ext": ext}))
    assert reg.getById("id://Root") is not None
    assert reg.getById("id://Ext") is not None

    removed = reg.removeSchema("runtime", "R", purgeIds=True)
    assert removed is True
    assert reg.getById("id://Root") is None
    # Note: purging also removes refs—it was registered via this doc
    assert reg.getById("id://Ext") is None


def test_compile_and_validate_happy_and_error_paths():
    reg = SchemaRegistry()
    root = {
        "$id": "id://User",
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    reg.addSchema(SchemaDoc(desc=d("rpc", "user@1"), schema=root))

    # OK
    reg.validate(namespace="rpc", name="user@1", instance={"name": "Ada", "age": 36})

    # Missing required
    with pytest.raises(ValidationError):
        reg.validate(namespace="rpc", name="user@1", instance={"age": 36})

    # Additional property not allowed
    with pytest.raises(ValidationError):
        reg.validate(namespace="rpc", name="user@1", instance={"name": "Ada", "extra": True})


def test_cache_usage_and_invalidation_on_add():
    reg = SchemaRegistry()
    root_v1: dict = {"$id": "id://C", "type": "number"}
    reg.addSchema(SchemaDoc(desc=d("runtime", "C", "1.0.0"), schema=root_v1))

    v1 = reg.getValidator("runtime", "C")
    v1(3.14)  # works

    # Add higher version should invalidate caches
    root_v2: dict = {"$id": "id://C", "type": "string"}  # same $id, same content would be OK, but let's change name & version
    reg.addSchema(SchemaDoc(desc=d("runtime", "C", "2.0.0", prio=1), schema=root_v2))

    assert ("runtime", "C", "2.0.0") in {(ns, name, ver) for ns, name, ver in reg.listSchema()}

    v2 = reg.getValidator("runtime", "C")
    with pytest.raises(Exception):
        v2(3.14)  # now string-only


def test_boolean_ref_is_indexed_but_not_walked_and_resolves():
    reg = SchemaRegistry()

    # External bool schema
    reg.addSchema(
        SchemaDoc(
            desc=d("global", "B"),
            schema={"$id": "id://HasRef", "$ref": "id://BoolTrue"},  # refers to boolean schema
            refs={"id://BoolTrue": True},
        )
    )

    # Validation should pass as True allows anything
    reg.validate(namespace="global", name="B", instance={"whatever": 1})

    # Now a False referenced by $ref should reject
    reg.addSchema(
        SchemaDoc(
            desc=d("global", "B2"),
            schema={"$id": "id://HasRef2", "$ref": "id://BoolFalse"},
            refs={"id://BoolFalse": False},
        )
    )
    with pytest.raises(ValidationError):
        reg.validate(namespace="global", name="B2", instance={"x": 1})


def test_pointer_escapes_tilde_and_slash():
    reg = SchemaRegistry()
    schema = {
        "$id": "id://Esc",
        "$defs": {
            "tilde~key": {"type": "string"},
            "slash/key": {"type": "integer"},
        },
        "allOf": [
            {"$ref": "#/$defs/tilde~0key"},
            {"$ref": "#/$defs/slash~1key"},
        ],
    }
    reg.addSchema(SchemaDoc(desc=d("mod", "Esc"), schema=schema))
    v = reg.getValidator("mod", "Esc")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("string")  # string ∧ integer cannot both pass


def test_boolean_id_with_fragment_left_unresolved():
    reg = SchemaRegistry()
    reg.addSchema(
        SchemaDoc(
            desc=d("g", "B"),
            schema={"$id": "id://Root", "$ref": "id://BTrue#/nope"},  # fragment on bool
            refs={"id://BTrue": True},
        )
    )
    # fastjsonschema will see a $ref it can't resolve (left as-is) and typically rejects anything
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="B", instance={"x": 1})


def test_circular_ref_is_left_unresolved_and_validation_fails():
    reg = SchemaRegistry()
    cyc = {
        "$id": "id://Cyc",
        "$defs": {"Self": {"$ref": "id://Cyc#/$defs/Self"}},
        "$ref": "id://Cyc#/$defs/Self",
    }
    reg.addSchema(SchemaDoc(desc=d("g", "Cyc"), schema=cyc))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="Cyc", instance=123)


def test_duplicate_anchor_in_same_doc_raises():
    reg = SchemaRegistry()
    dup = {
        "$id": "id://DupA",
        "A": {"$anchor": "AA", "type": "string"},
        "B": {"$anchor": "AA", "type": "string"},  # even if “same” content, still a different node
    }
    with pytest.raises(ValueError):
        reg.addSchema(SchemaDoc(desc=d("mod", "Dup"), schema=dup))


def test_purge_ids_keeps_shared_identical_refs_if_still_registered():
    reg = SchemaRegistry()
    ext = {"$id": "id://Shared", "$defs": {"X": {"type": "integer"}}}

    reg.addSchema(SchemaDoc(desc=d("m", "A"), schema={"$id": "id://A"}, refs={"id://Shared": ext}))
    reg.addSchema(SchemaDoc(desc=d("m", "B"), schema={"$id": "id://B"}, refs={"id://Shared": ext}))

    assert reg.getById("id://Shared") is not None
    reg.removeSchema("m", "A", purgeIds=True)

    # Decide on policy: EITHER still present (preferred), OR gone. If you keep it:
    assert reg.getById("id://Shared") is not None


def test_getById_returns_boolean_schema_as_is():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g", "T"), schema={"$id": "id://T"}, refs={"id://Bool": True}))
    # Manually index the bool root too
    reg.addSchema(SchemaDoc(desc=d("g", "BoolRoot"), schema=True))
    assert reg.getById("id://T") is not None
    assert reg.getById("id://NonExistent") is None
    # Direct bool id:
    reg.addSchema(SchemaDoc(desc=d("g", "BR"), schema=True, refs={"id://OnlyBool": True}))
    assert reg.getById("id://OnlyBool") is True


def test_local_anchor_cache_multiple_hits():
    reg = SchemaRegistry()
    schema = {
        "$id": "id://AnchorCache",
        "N1": {"$anchor": "A", "type": "number"},
        "allOf": [{"$ref": "#A"}, {"$ref": "#A"}, {"$ref": "#A"}],
    }
    reg.addSchema(SchemaDoc(desc=d("g", "AC"), schema=schema))
    v = reg.getValidator("g", "AC")
    v(3.14)
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("nope")


def test_thread_safety_smoke():
    reg = SchemaRegistry()
    base: dict = {"$id": "id://T", "type": "integer"}
    reg.addSchema(SchemaDoc(desc=d("g", "T"), schema=base))

    errs = []

    def reader():
        try:
            for _ in range(100):
                v = reg.getValidator("g", "T")
                v(1)
        except Exception as e:
            errs.append(e)

    def writer():
        s2: dict = {"$id": "id://T", "type": "string"}
        reg.addSchema(SchemaDoc(desc=d("g", "T"), schema=s2, refs={}))
    
    t1 = threading.Thread(target=reader)
    t2 = threading.Thread(target=writer)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errs


def test_id_collision_same_content_different_key_order_ok():
    reg = SchemaRegistry()
    s1 = {"$id": "id://K", "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}}
    s2 = {"$id": "id://K", "properties": {"b": {"type": "string"}, "a": {"type": "integer"}}, "type": "object"}
    reg.addSchema(SchemaDoc(desc=d("g", "K1"), schema=s1))
    reg.addSchema(SchemaDoc(desc=d("g", "K2"), schema=s2))  # should not raise


def test_compile_all_warms_cache():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g", "A"), schema={"$id": "id://A", "type": "string"}))
    reg.addSchema(SchemaDoc(desc=d("g", "B"), schema={"$id": "id://B", "type": "integer"}))
    reg.compileAll()
    reg.validate(namespace="g", name="A", instance="x")
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="A", instance=1)


def test_same_ref_id_from_two_docs_identical_ok_different_raises():
    reg = SchemaRegistry()
    ext1: dict = {"$id": "id://DUP", "type": "string"}
    ext2_same: dict = {"$id": "id://DUP", "type": "string"}
    ext2_diff: dict = {"$id": "id://DUP", "type": "integer"}

    reg.addSchema(SchemaDoc(desc=d("g", "A"), schema={"$id": "id://A"}, refs={"id://DUP": ext1}))
    reg.addSchema(SchemaDoc(desc=d("g", "B"), schema={"$id": "id://B"}, refs={"id://DUP": ext2_same}))  # ok
    with pytest.raises(ValueError):
        reg.addSchema(SchemaDoc(desc=d("g", "C"), schema={"$id": "id://C"}, refs={"id://DUP": ext2_diff}))


def test_prerelease_is_lower_than_stable_and_priority_breaks_ties():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g", "V", "1.2.3-alpha.1"), schema={"$id":"id://V","type":"integer"}))
    # Stable should supersede prerelease even with same prio
    reg.addSchema(SchemaDoc(desc=d("g", "V", "1.2.3"), schema={"$id":"id://V","type":"string"}))
    v = reg.getValidator("g","V")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(123)  # now string-only

    # If version equal, higher priority wins
    reg.addSchema(SchemaDoc(desc=d("g", "V", "1.2.3", prio=1), schema={"$id":"id://V","type":"number"}))
    v2 = reg.getValidator("g","V")
    v2(3.14)


def test_lower_version_add_is_ignored():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","X","2.0.0"), schema={"$id":"id://X","type":"string"}))
    # Older version should be ignored
    reg.addSchema(SchemaDoc(desc=d("g","X","1.0.0"), schema={"$id":"id://X","type":"integer"}))
    v = reg.getValidator("g","X")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(42)

# --- JSON Pointer: arrays and bad paths ---

def test_pointer_into_array_and_oob():
    reg = SchemaRegistry()
    schema = {
        "$id": "id://Arr",
        "$defs": {"L": [{"type":"string"}, {"type":"integer"}]},
        "allOf": [{"$ref":"#/$defs/L/0"},{"$ref":"#/$defs/L/1"}],
    }
    reg.addSchema(SchemaDoc(desc=d("g","Arr"), schema=schema))
    v = reg.getValidator("g","Arr")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("s")  # cannot satisfy both

    bad = {"$id":"id://Arr2", "$ref":"#/$defs/L/99", "$defs":{"L":[{"type":"string"}]}}
    reg.addSchema(SchemaDoc(desc=d("g","Arr2"), schema=bad))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="Arr2", instance="anything")

# --- Cross-document cycles / anchors vs pointers ---

def test_cross_doc_cycle_left_unresolved():
    reg = SchemaRegistry()
    a: dict = {"$id":"id://A", "$ref":"id://B"}
    b: dict = {"$id":"id://B", "$ref":"id://A"}
    reg.addSchema(SchemaDoc(desc=d("g","A"), schema={"$id":"id://RootA", "$ref":"id://A"}, refs={"id://A":a,"id://B":b}))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="A", instance=1)


def test_external_anchor_precedence_over_pointer():
    reg = SchemaRegistry()
    lib = {"$id":"id://L", "$defs":{"Target":{"type":"integer"}}, "Node":{"$anchor":"A","type":"string"}}
    root = {"$id":"id://R", "allOf":[{"$ref":"id://L#A"}]}
    reg.addSchema(SchemaDoc(desc=d("g","R"), schema=root, refs={"id://L":lib}))
    v = reg.getValidator("g","R")
    v("ok")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(123)


def test_nested_id_changes_base_for_anchors():
    reg = SchemaRegistry()
    nested = {
        "$id":"id://Outer",
        "$defs":{"Inner":{"$id":"id://Inner","Sub":{"$anchor":"Z","type":"boolean"}}},
        "allOf":[{"$ref":"id://Inner#Z"}],
    }
    reg.addSchema(SchemaDoc(desc=d("g","N"), schema=nested))
    v = reg.getValidator("g","N")
    v(True)
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("no")

# --- Indexing / listing / cache semantics ---

def test_schema_without_id_not_indexed_but_usable():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","NoId"), schema={"type":"integer"}))
    assert reg.getById("id://whatever") is None
    reg.validate(namespace="g", name="NoId", instance=7)


def test_list_schema_filters_by_namespace_and_shows_latest():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("A","K","1.0.0"), schema={"$id":"id://K","type":"string"}))
    reg.addSchema(SchemaDoc(desc=d("B","K","1.0.0"), schema={"$id":"id://K2","type":"integer"}))
    reg.addSchema(SchemaDoc(desc=d("A","K","2.0.0"), schema={"$id":"id://K","type":"number"}))
    all_items = reg.listSchema()
    only_A = reg.listSchema(namespace="A")
    assert ("A","K","2.0.0") in all_items and ("A","K","1.0.0") not in all_items
    assert all(x[0]=="A" for x in only_A)


def test_getvalidator_returns_cached_until_invalidation():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","C","1.0.0"), schema={"$id":"id://C","type":"integer"}))
    v1 = reg.getValidator("g","C")
    v1(5)
    # supersede -> cache invalidates
    reg.addSchema(SchemaDoc(desc=d("g","C","2.0.0", prio=1), schema={"$id":"id://C","type":"string"}))
    v2 = reg.getValidator("g","C")
    assert v1 is not v2
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v2(5)

# --- Purge nuances and unresolved behavior ---

def test_unresolved_ignores_missing_anchor_if_base_exists():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","Lib"), schema={"$id":"id://Lib","type":"object"}))
    reg.addSchema(SchemaDoc(desc=d("g","Use"), schema={"$id":"id://Use", "$ref":"id://Lib#Nope"}))
    assert reg.findUnresolvedRefs() == []  # base present, anchor ignored by report


def test_remove_without_purge_keeps_ids():
    reg = SchemaRegistry()
    root: dict = {"$id":"id://Keep","type":"string"}
    reg.addSchema(SchemaDoc(desc=d("g","S1"), schema=root))
    assert reg.getById("id://Keep") is not None
    reg.removeSchema("g","S1", purgeIds=False)
    # id still present
    assert reg.getById("id://Keep") is not None


def test_pointer_to_invalid_path_on_external_id_left_unresolved():
    reg = SchemaRegistry()
    lib = {"$id":"id://LIB","$defs":{"ok":{"type":"number"}}}
    root:dict = {"$id":"id://R","$ref":"id://LIB#/$defs/missing"}
    reg.addSchema(SchemaDoc(desc=d("g","R"), schema=root, refs={"id://LIB": lib}))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="R", instance=123)

# --- SemVer/priority edges ---

def test_prerelease_can_outrank_lower_patch_stable():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","V","1.2.3"), schema={"$id":"id://V","type":"integer"}))
    # Higher patch, prerelease -> should outrank 1.2.3 stable (1.2.4-alpha > 1.2.3)
    reg.addSchema(SchemaDoc(desc=d("g","V","1.2.4-alpha.1"), schema={"$id":"id://V","type":"string"}))
    v = reg.getValidator("g","V")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(123)  # now string-only


def test_priority_breaks_exact_version_ties():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","Tie","2.0.0", prio=0), schema={"$id":"id://Tie","type":"integer"}))
    reg.addSchema(SchemaDoc(desc=d("g","Tie","2.0.0", prio=1), schema={"$id":"id://Tie","type":"string"}))
    v = reg.getValidator("g","Tie")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(10)

# --- Local $ref to the entire root ---

def test_local_ref_to_document_root_via_hash():
    reg = SchemaRegistry()
    root = {"$id":"id://R", "allOf":[{"$ref":"#"}]}  # should just be the doc itself
    reg.addSchema(SchemaDoc(desc=d("g","HashRoot"), schema=root))
    # Valid for empty object (no constraints)
    reg.validate(namespace="g", name="HashRoot", instance={})

# --- Nested $id duplication rules ---

def test_duplicate_nested_id_same_content_ok_different_raises():
    reg = SchemaRegistry()
    s1 = {
        "$id":"id://Base",
        "A":{"$id":"id://Nested","type":"number"},
    }
    s2_same = {
        "$id":"id://Base2",
        "B":{"$id":"id://Nested","type":"number"},  # identical node under same $id
    }
    s2_diff = {
        "$id":"id://Base3",
        "C":{"$id":"id://Nested","type":"string"},  # different node for same $id
    }
    reg.addSchema(SchemaDoc(desc=d("g","N1"), schema=s1))
    reg.addSchema(SchemaDoc(desc=d("g","N2"), schema=s2_same))  # OK
    with pytest.raises(ValueError):
        reg.addSchema(SchemaDoc(desc=d("g","N3"), schema=s2_diff))

# --- Immutability of refs and anchors after registration ---

def test_mutating_refs_after_add_does_not_affect_registry():
    reg = SchemaRegistry()
    lib = {"$id":"id://L", "Node":{"$anchor":"A","type":"integer"}}
    root: dict = {"$id":"id://R", "$ref":"id://L#A"}
    reg.addSchema(SchemaDoc(desc=d("g","UseLib"), schema=root, refs={"id://L": lib}))
    v = reg.getValidator("g","UseLib")
    v(5)
    # mutate original lib after add; registry should be unaffected
    lib["Node"]["type"] = "string"
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("not-an-int")

# --- Pointer corner-cases ---

def test_pointer_with_non_integer_index_in_array_path_left_unresolved():
    reg = SchemaRegistry()
    sch = {
        "$id":"id://Arr",
        "$defs":{"L":[{"type":"string"}]},
        "$ref":"#/$defs/L/not-an-index",  # invalid array index -> unresolved
    }
    reg.addSchema(SchemaDoc(desc=d("g","Arr"), schema=sch))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="Arr", instance="anything")

# --- Admin APIs and error paths ---

def test_clear_resets_everything():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","C1"), schema={"$id":"id://C1","type":"integer"}))
    assert reg.hasSchema("g","C1")
    assert reg.getById("id://C1") is not None
    reg.clear()
    assert not reg.hasSchema("g","C1")
    assert reg.getById("id://C1") is None
    with pytest.raises(KeyError):
        reg.getValidator("g","C1")  # no such schema


def test_hasSchema_and_remove_nonexistent():
    reg = SchemaRegistry()
    assert not reg.hasSchema("x","nope")
    assert reg.removeSchema("x","nope", purgeIds=True) is False


def test_compileall_populates_cache_for_all():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","A"), schema={"$id":"id://A","type":"integer"}))
    reg.addSchema(SchemaDoc(desc=d("g","B"), schema={"$id":"id://B","type":"string"}))
    reg.compileAll()
    # Should be able to validate both without compiling on the fly
    reg.validate(namespace="g", name="A", instance=1)
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="B", instance=123)


def test_list_schema_ordering_is_stable_and_sorted():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("b","y","1.0.0"), schema={"$id":"id://y","type":"integer"}))
    reg.addSchema(SchemaDoc(desc=d("a","x","1.0.0"), schema={"$id":"id://x","type":"integer"}))
    reg.addSchema(SchemaDoc(desc=d("a","w","1.0.0"), schema={"$id":"id://w","type":"integer"}))
    items = reg.listSchema()
    assert items == sorted(items, key=lambda t: (t[0], t[1]))


def test_external_pointer_escapes_tilde_and_slash():
    reg = SchemaRegistry()
    lib = {
        "$id": "id://EscLib",
        "$defs": {
            "tilde~key": {"type": "string"},
            "slash/key": {"type": "integer"},
        },
    }
    root = {
        "$id": "id://UsesEsc",
        "allOf": [
            {"$ref": "id://EscLib#/$defs/tilde~0key"},
            {"$ref": "id://EscLib#/$defs/slash~1key"},
        ],
    }
    reg.addSchema(SchemaDoc(desc=d("g", "UsesEsc"), schema=root, refs={"id://EscLib": lib}))
    v = reg.getValidator("g", "UsesEsc")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("x")   # cannot be both string and integer


def test_boolean_ref_with_other_constraints_in_allOf():
    reg = SchemaRegistry()
    root = {
        "$id": "id://NumAndTrue",
        "allOf": [{"type": "number"}, {"$ref": "id://BTrue"}],
    }
    reg.addSchema(SchemaDoc(desc=d("g", "NAndT"), schema=root, refs={"id://BTrue": True}))
    v = reg.getValidator("g", "NAndT")
    v(3.14)  # still must be number
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("nope")


def test_supersede_allows_nested_id_content_change_within_same_doc():
    reg = SchemaRegistry()
    v1 = {
        "$id": "id://RootN",
        "A": {"$id": "id://NestedN", "type": "number"},
    }
    v2 = {
        "$id": "id://RootN",
        "A": {"$id": "id://NestedN", "type": "string"},  # different content under same nested $id
    }
    reg.addSchema(SchemaDoc(desc=d("g", "Nest", "1.0.0"), schema=v1))
    reg.addSchema(SchemaDoc(desc=d("g", "Nest", "2.0.0"), schema=v2))  # allowed by supersede policy
    # global index should now reflect the new nested content
    got = reg.getById("id://NestedN")
    assert isinstance(got, dict) and got.get("type") == "string"


def test_remove_with_purge_drops_anchors_from_refs():
    reg = SchemaRegistry()
    lib = {"$id": "id://L", "N": {"$anchor": "A", "type": "integer"}}
    root: dict = {"$id": "id://R", "$ref": "id://L#A"}
    reg.addSchema(SchemaDoc(desc=d("g", "Use"), schema=root, refs={"id://L": lib}))
    # sanity: resolves and validates
    reg.validate(namespace="g", name="Use", instance=10)
    # purge the only doc that brought in id://L and its anchor
    reg.removeSchema("g", "Use", purgeIds=True)
    # now the anchor should be gone → validation fails because $ref can't resolve
    reg.addSchema(SchemaDoc(desc=d("g", "Probe"), schema=root, refs={}))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="Probe", instance=10)


def test_findunresolved_ignores_local_anchor_ref():
    reg = SchemaRegistry()
    root = {"$id": "id://A", "Node": {"$anchor": "X", "type": "number"}, "allOf": [{"$ref": "#X"}]}
    reg.addSchema(SchemaDoc(desc=d("g", "A"), schema=root))
    assert reg.findUnresolvedRefs() == []


def test_ref_to_document_root_inside_nested_schema_is_noop():
    reg = SchemaRegistry()
    root = {
        "$id": "id://SelfRefNested",
        "type": "object",
        "properties": {"p": {"$ref": "#"}},  # treated as {} (no extra constraints)
        "additionalProperties": True,
    }
    reg.addSchema(SchemaDoc(desc=d("g", "SRN"), schema=root))
    reg.validate(namespace="g", name="SRN", instance={"p": 1, "q": "ok"})


def test_addSchemas_bulk_inserts_all():
    reg = SchemaRegistry()
    docs = [
        SchemaDoc(desc=d("g", "BulkA"), schema={"$id": "id://BulkA", "type": "string"}),
        SchemaDoc(desc=d("g", "BulkB"), schema={"$id": "id://BulkB", "type": "integer"}),
    ]
    reg.addSchemas(docs)
    reg.validate(namespace="g", name="BulkA", instance="x")
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="BulkB", instance="x")


def test_self_absolute_pointer_to_own_defs():
    reg = SchemaRegistry()
    root = {
        "$id": "id://SelfAbs",
        "$defs": {"S": {"type": "string"}},
        "$ref": "id://SelfAbs#/$defs/S",  # absolute pointer to own $defs
    }
    reg.addSchema(SchemaDoc(desc=d("g", "SelfAbs"), schema=root))
    v = reg.getValidator("g", "SelfAbs")
    v("ok")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(123)


def test_priority_can_outrank_higher_version():
    reg = SchemaRegistry()
    # Lower version but higher priority should win given current key ordering
    reg.addSchema(SchemaDoc(desc=d("g","P","1.0.0", prio=10), schema={"$id":"id://P","type":"string"}))
    reg.addSchema(SchemaDoc(desc=d("g","P","2.0.0", prio=0), schema={"$id":"id://P","type":"integer"}))
    v = reg.getValidator("g","P")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(123)  # still string-only


def test_duplicate_nested_id_within_single_doc_identical_ok_different_raises():
    reg = SchemaRegistry()
    same_twice = {
        "$id":"id://Base",
        "X":{"$id":"id://N","type":"number"},
        "Y":{"$id":"id://N","type":"number"},
    }
    reg.addSchema(SchemaDoc(desc=d("g","D1"), schema=same_twice))  # identical nested nodes OK

    diff_twice = {
        "$id":"id://Base2",
        "X":{"$id":"id://N2","type":"number"},
        "Y":{"$id":"id://N2","type":"string"},
    }
    with pytest.raises(ValueError):
        reg.addSchema(SchemaDoc(desc=d("g","D2"), schema=diff_twice))


def test_anchor_inside_array_element_resolves():
    reg = SchemaRegistry()
    lib = {
        "$id":"id://LArr",
        "defs":[{"$anchor":"A","type":"integer"}, {"type":"string"}],
    }
    root: dict = {"$id":"id://UseArr","$ref":"id://LArr#A"}
    reg.addSchema(SchemaDoc(desc=d("g","UseArr"), schema=root, refs={"id://LArr": lib}))
    v = reg.getValidator("g","UseArr")
    v(10)
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v("s")


def test_findunresolved_reports_each_id_once_even_if_referenced_multiple_times():
    reg = SchemaRegistry()
    root = {"$id":"id://R",
            "allOf":[{"$ref":"id://Missing"}, {"$ref":"id://Missing#A"}, {"$ref":"id://Missing#/$defs/Nope"}]}
    reg.addSchema(SchemaDoc(desc=d("g","R"), schema=root))
    assert reg.findUnresolvedRefs() == ["id://Missing"]


def test_local_anchor_self_cycle_left_unresolved_and_validation_fails():
    reg = SchemaRegistry()
    cyc = {
        "$id":"id://SelfA",
        "A":{"$anchor":"A","$ref":"#A"},
        "$ref":"#A",
    }
    reg.addSchema(SchemaDoc(desc=d("g","SelfA"), schema=cyc))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="SelfA", instance=1)


def test_http_scheme_ids_work_like_id_scheme():
    reg = SchemaRegistry()
    lib = {"$id":"http://example.com/s", "$defs":{"S":{"type":"string"}}}
    root: dict = {"$id":"id://R", "$ref":"http://example.com/s#/$defs/S"}
    reg.addSchema(SchemaDoc(desc=d("g","Http"), schema=root, refs={"http://example.com/s": lib}))
    v = reg.getValidator("g","Http")
    v("ok")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(1)


def test_purge_keeps_shared_identical_anchor_if_other_doc_still_registers_it():
    reg = SchemaRegistry()
    lib = {"$id":"id://SharedA", "N":{"$anchor":"A","type":"integer"}}
    # Two docs bring the same id + anchor
    reg.addSchema(SchemaDoc(desc=d("g","U1"), schema={"$id":"id://U1", "$ref":"id://SharedA#A"}, refs={"id://SharedA": lib}))
    reg.addSchema(SchemaDoc(desc=d("g","U2"), schema={"$id":"id://U2", "$ref":"id://SharedA#A"}, refs={"id://SharedA": lib}))
    # Remove one with purge -> anchor should still be present (shared)
    reg.removeSchema("g","U1", purgeIds=True)
    reg.validate(namespace="g", name="U2", instance=5)  # still resolves


def test_getschema_returns_deepcopy_for_lists_too():
    reg = SchemaRegistry()
    root = {"$id":"id://ListRoot", "type":"object", "required":["a"], "properties":{"a":{"type":"integer"}}}
    reg.addSchema(SchemaDoc(desc=d("g","ListDoc"), schema=root))
    got = cast(dict, reg.getSchema("g","ListDoc"))
    assert got == root and got is not root
    got["required"].append("b")
    again = cast(dict, reg.getSchema("g","ListDoc"))
    assert again["required"] == ["a"]  # unchanged


def test_absolute_ref_to_boolean_id_no_fragment_ok():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","UsesTrue"), schema={"$id":"id://R","$ref":"id://T"}, refs={"id://T": True}))
    reg.validate(namespace="g", name="UsesTrue", instance={"anything": 1})
    reg.addSchema(SchemaDoc(desc=d("g","UsesFalse"), schema={"$id":"id://R2","$ref":"id://F"}, refs={"id://F": False}))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="UsesFalse", instance="nope")

# --- Absolute id missing stays unresolved and fails at validate ---

def test_absolute_ref_to_missing_id_fails_on_validate():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","MissingUse"), schema={"$id":"id://U","$ref":"id://NoSuch"}))
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="MissingUse", instance="anything")

# --- Superseding can change anchors (same (ns,name) newer version wins) ---

def test_supersede_updates_anchor_table_for_same_doc_identity():
    reg = SchemaRegistry()
    v1 = {"$id":"id://L","N":{"$anchor":"A","type":"string"}}
    v2 = {"$id":"id://L","N":{"$anchor":"A","type":"integer"}}
    reg.addSchema(SchemaDoc(desc=d("g","Use", "1.0.0"), schema={"$id":"id://R","allOf":[{"$ref":"id://L#A"}]}, refs={"id://L": v1}))
    # resolves as string
    v = reg.getValidator("g","Use")
    v("ok")
    # supersede same (ns,name) with newer version that changes anchor target
    reg.addSchema(SchemaDoc(desc=d("g","Use", "2.0.0"), schema={"$id":"id://R2","allOf":[{"$ref":"id://L#A"}]}, refs={"id://L": v2}))
    v2_fn = reg.getValidator("g","Use")
    v2_fn(123)  # now integer
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v2_fn("nope")

# --- Non-string $id/$anchor are ignored gracefully during indexing ---

def test_non_string_id_and_anchor_are_ignored():
    reg = SchemaRegistry()
    root = {
        "$id": "id://Root",
        "badId": {"$id": 123, "type": "string"},        # ignored as an $id
        "badAnchorHost": {"$anchor": 456, "type": "integer"},  # no baseId -> ignored
    }
    reg.addSchema(SchemaDoc(desc=d("g","BadMeta"), schema=root))
    # Only the string root id is present
    assert reg.getById("id://Root") is not None
    assert reg.getById("123") is None

# --- SemVer edge: invalid version string sorts as lowest and gets outranked ---

def test_invalid_version_is_outclassed_by_valid_version():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","V","not-a-semver"), schema={"$id":"id://V","type":"integer"}))
    reg.addSchema(SchemaDoc(desc=d("g","V","0.0.1"), schema={"$id":"id://V","type":"string"}))
    v = reg.getValidator("g","V")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(123)  # string-only now

# --- Priority negative/positive works: higher number wins even with lower version ---

def test_priority_with_negative_and_positive_values():
    reg = SchemaRegistry()
    reg.addSchema(SchemaDoc(desc=d("g","P","2.0.0", prio=-5), schema={"$id":"id://P","type":"integer"}))
    reg.addSchema(SchemaDoc(desc=d("g","P","1.0.0", prio=7), schema={"$id":"id://P","type":"string"}))
    v = reg.getValidator("g","P")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(1)

# --- JSON Pointer into arrays with leading zeros in index token ---

def test_pointer_array_index_with_leading_zero_is_ok():
    reg = SchemaRegistry()
    root = {
        "$id":"id://LeadZero",
        "$defs":{"arr":[{"type":"string"}]},
        "$ref":"#/$defs/arr/00"  # "00" -> index 0
    }
    reg.addSchema(SchemaDoc(desc=d("g","LeadZero"), schema=root))
    v = reg.getValidator("g","LeadZero")
    v("hello")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        v(1)

# --- $ref to own $id (absolute self) should work and not loop ---

def test_absolute_self_ref_to_own_id():
    reg = SchemaRegistry()
    root = {
        "$id":"id://Self",
        "type":"object",
        "properties":{"a":{"type":"integer"}},
        "allOf":[{"$ref":"id://Self"}],  # absolute self-ref
        "additionalProperties": False,
    }
    reg.addSchema(SchemaDoc(desc=d("g","SelfAbs"), schema=root))
    reg.validate(namespace="g", name="SelfAbs", instance={"a": 1})
    with pytest.raises(ValidationError):
        reg.validate(namespace="g", name="SelfAbs", instance={"a":"nope","b":True})

# --- addSchemas handles empty list without error ---

def test_addschemas_accepts_empty_list():
    reg = SchemaRegistry()
    reg.addSchemas([])
    assert reg.listSchema() == []

# --- getSchema deepcopy also protects nested lists/dicts mutations (extra guard) ---

def test_getschema_deepcopy_isolation_nested_mutations():
    reg = SchemaRegistry()
    root = {"$id":"id://Deep", "type":"object", "properties":{"a":{"type":"array","items":[{"type":"integer"}]}}}
    reg.addSchema(SchemaDoc(desc=d("g","Deep"), schema=root))
    got = cast(dict, reg.getSchema("g","Deep"))
    got["properties"]["a"]["items"].append({"type":"string"})
    again = cast(dict, reg.getSchema("g","Deep"))
    assert again["properties"]["a"]["items"] == [{"type":"integer"}]

# --- Multiple missing absolute ids reported once each; anchors ignored in report ---

def test_unresolved_reports_distinct_ids_only():
    reg = SchemaRegistry()
    root = {"$id":"id://R",
            "allOf":[{"$ref":"id://X#A"},{"$ref":"id://Y#/$defs/Z"},{"$ref":"id://X#/$defs/Q"}]}
    reg.addSchema(SchemaDoc(desc=d("g","R"), schema=root))
    assert reg.findUnresolvedRefs() == ["id://X", "id://Y"]

