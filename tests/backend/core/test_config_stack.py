# tests/backend/core/test_config_stack.py
from __future__ import annotations

import copy
import pytest

from backend.core.config_stack import (
    mergeWithStrategy,
    ConfigLayer,
    ConfigStack,
)


# -------- mergeWithStrategy (dict) --------

def test_merge_replace_dict():
    left = {"a": 1, "b": {"x": 1, "y": 2}}
    right = {"__merge": "replace", "b": {"z": 3}, "c": 9}
    out = mergeWithStrategy(left, right)
    # whole object replaced (minus control key)
    assert out == {"b": {"z": 3}, "c": 9}


def test_merge_deep_default():
    left = {"a": 1, "b": {"x": 1, "y": 2}}
    right = {"b": {"y": 5, "z": 9}, "c": 7}
    out = mergeWithStrategy(left, right)
    assert out == {"a": 1, "b": {"x": 1, "y": 5, "z": 9}, "c": 7}


# -------- mergeWithStrategy (lists) --------

def test_list_replace_default_when_both_lists():
    assert mergeWithStrategy([1, 2], [3, 4]) == [3, 4]


def test_list_side_channel_append_prepend_unique():
    left = {"items": [1, 2, 3]}
    # append
    right = {"items": [3, 4], "items__merge": "append"}
    out = mergeWithStrategy(left, right)
    assert out["items"] == [1, 2, 3] + [3, 4]

    # prepend
    right = {"items": [0], "items__merge": "prepend"}
    out = mergeWithStrategy(left, right)
    assert out["items"] == [0] + [1, 2, 3]

    # uniqueAppend
    right = {"items": [2, 99], "items__merge": "uniqueAppend"}
    out = mergeWithStrategy(left, right)
    assert out["items"] == [1, 2, 3, 99]


def test_list_side_channel_key_does_not_leak_into_output():
    left = {"items": [1]}
    right = {"items": [2], "items__merge": "append"}
    out = mergeWithStrategy(left, right)
    assert "items__merge" not in out
    assert out["items"] == [1, 2]


def test_merge_does_not_mutate_inputs():
    left = {"a": [1], "b": {"x": 1}}
    right = {"a": [2], "a__merge": "append", "b": {"y": 2}}
    left_copy = copy.deepcopy(left)
    right_copy = copy.deepcopy(right)
    _ = mergeWithStrategy(left, right)
    assert left == left_copy
    assert right == right_copy


# -------- ConfigStack / ConfigView --------

def test_precedence_global_to_runtime():
    stack = ConfigStack()
    stack.addLayer(ConfigLayer(name="global", scope="global", data={"v": 1, "arr": [1]}))
    stack.addLayer(ConfigLayer(name="modpack", scope="modpack", data={"v": 2}))
    stack.addLayer(ConfigLayer(name="game", scope="game", data={"v": 3}))
    stack.addLayer(ConfigLayer(name="mod", scope="mod", data={"v": 4}))
    stack.addLayer(ConfigLayer(name="asset", scope="asset", data={"v": 5}))
    stack.addLayer(ConfigLayer(name="runtime", scope="runtime", data={"v": 6, "arr": [2], "arr__merge": "append"}))

    view = stack.view()
    eff = view.effective()
    # runtime at highest precedence
    assert eff["v"] == 6
    # list append honored at highest layer
    assert eff["arr"] == [1, 2]


def test_view_tag_filtering_by_mod_and_asset():
    stack = ConfigStack()
    stack.addLayer(ConfigLayer(name="global", scope="global", data={"k": "g"}))
    stack.addLayer(ConfigLayer(name="mod.A", scope="mod", data={"k": "A"}, tags={"modId": "A"}))
    stack.addLayer(ConfigLayer(name="mod.B", scope="mod", data={"k": "B"}, tags={"modId": "B"}))
    stack.addLayer(ConfigLayer(name="asset.X", scope="asset", data={"a": 1}, tags={"assetId": "X"}))
    stack.addLayer(ConfigLayer(name="asset.Y", scope="asset", data={"a": 2}, tags={"assetId": "Y"}))

    # Unfiltered view sees only global in this key path
    assert stack.view().get("k") == "g"

    # mod-specific view
    assert stack.view(modId="A").get("k") == "A"
    assert stack.view(modId="B").get("k") == "B"

    # asset-specific view
    assert stack.view(assetId="X").get("a") == 1
    assert stack.view(assetId="Y").get("a") == 2

    # both filters applied: if a layer has modId AND assetId, it must match both (implicitly)
    stack.addLayer(ConfigLayer(
        name="mod.A.asset.X",
        scope="asset",
        data={"mx": 42},
        tags={"modId": "A", "assetId": "X"},
    ))
    assert stack.view(modId="A", assetId="X").get("mx") == 42
    assert stack.view(modId="A", assetId="Y").get("mx") is None
    assert stack.view(modId="B", assetId="X").get("mx") is None


def test_cache_invalidation_on_add_remove_setLayers():
    stack = ConfigStack()
    stack.addLayer(ConfigLayer(name="global", scope="global", data={"foo": 1}))
    view = stack.view()
    assert view.get("foo") == 1

    # add layer → cache must invalidate
    stack.addLayer(ConfigLayer(name="game", scope="game", data={"foo": 2}))
    assert view.get("foo") == 2

    # remove layer → cache must invalidate again
    assert stack.removeLayer("game") is True
    assert view.get("foo") == 1

    # setLayers → cache must invalidate again
    stack.setLayers([ConfigLayer(name="runtime", scope="runtime", data={"foo": 9})])
    assert view.get("foo") == 9


def test_list_strategy_multiple_keys_do_not_cross_pollute():
    stack = ConfigStack()
    stack.addLayer(ConfigLayer(name="global", scope="global", data={"a": [1], "b": [10]}))
    stack.addLayer(ConfigLayer(name="runtime", scope="runtime", data={
        "a": [2], "a__merge": "append",
        "b": [0], "b__merge": "prepend",
    }))
    eff = stack.view().effective()
    assert eff["a"] == [1, 2]
    assert eff["b"] == [0, 10]


def test_list_strategy_key_without_list_is_ignored():
    # Ensure harmless if a user mistakenly sets "<key>__merge" without a list payload.
    left = {"a": 1}
    right = {"a__merge": "append", "a": 2}  # not a list; falls through to scalar replace
    out = mergeWithStrategy(left, right)
    assert out["a"] == 2
    assert "a__merge" not in out


def test_per_key_wrapper_merge():
    left = {"mods": ["a"]}
    right = {"mods": {"__value": ["b"], "__merge": "append"}}
    assert mergeWithStrategy(left, right) == {"mods": ["a", "b"]}


def test_top_level_wrapper_requires_list_left():
    with pytest.raises(TypeError):
        mergeWithStrategy({}, {"__value": ["x"], "__merge": "append"})


def test_top_level_wrapper_on_list_left_ok():
    assert mergeWithStrategy(["a"], {"__value": ["b"], "__merge": "prepend"}) == ["b", "a"]


def test_invalid_merge_on_object():
    with pytest.raises(ValueError):
        mergeWithStrategy({}, {"__merge": "apend", "k": 1})


def test_dict_replace_deepcopy():
    right = {"__merge": "replace", "x": {"y": 1}}
    out = mergeWithStrategy({"x": {}}, right)
    assert out == {"x": {"y": 1}}
    right["x"]["y"] = 2
    assert out == {"x": {"y": 1}}  # mutation does not leak


def test_per_key_wrapper_prepend():
    left  = {"assets": ["base"]}
    right = {"assets": {"__value": ["dlc"], "__merge": "prepend"}}
    assert mergeWithStrategy(left, right) == {"assets": ["dlc", "base"]}


def test_unique_append_unhashable():
    left  = {"rows": [{"id":1}]}
    right = {"rows": [{"id":1}, {"id":2}], "rows__merge":"uniqueAppend"}
    out = mergeWithStrategy(left, right)
    assert out["rows"] == [{"id":1}, {"id":2}]


def test_reserved_key_guard_raises_on_missing_base():
    with pytest.raises(ValueError):
        mergeWithStrategy({}, {"ghost__merge": "append"})  # no "ghost" key present
