# backend/core/utils.py
from __future__ import annotations
import copy, json
from typing import Any, TypeVar

from backend.core.jsonutils import tryJSONify

__all__ = ["deepCopy", "deepEquals"]

T = TypeVar("T")



def deepCopy(value: T, *, strict: bool = True) -> T:
    """
    Safely deep-copies JSON-like data.
    
      - strict=True (default): raises on any copy failure.
      - strict=False: first attempts a structured coercion via tryJSONify
        (cycle/depth-safe) and returns that deep copy. If that somehow fails,
        attempts a JSON roundtrip. If that also fails, raises.
    
    Notes:
      • tryJSONify may coerce types to JSON-safe forms (e.g., sets/tuples → lists,
        bytes → {"__b64__": ...}); this is intentional in non-strict mode.
      • copy.deepcopy handles cycles; JSON roundtrip does not; tryJSONify guards them.
    """
    try:
        return copy.deepcopy(value)
    except Exception as err1:
        if strict:
            raise RuntimeError(f"deepCopy failed - {err1.__class__.__name__} {err1}") from err1

        # 1) Structured, cycle-safe coercion to a deep copy of containers
        try:
            coerced = tryJSONify(value, _maxDepth=None) # unlimited depth
            return coerced
        except Exception as err2:
            last = err2
        
        # 2) Deterministic JSON roundtrip which may drop cycles and coerce types
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
        except Exception as err3:
            raise RuntimeError(
                 "deepCopy failed via copy.deepcopy, tryJSONify, and JSON roundtrip;"
                f" copy.deepcopy error={err1.__class__.__name__}: {err1};"
                f" tryJSONify error={last.__class__.__name__}: {last};"
                f" json.loads/dumps error={err3.__class__.__name__}: {err3}"
            ) from err3



def deepEquals(first: Any, second: Any, *, strict: bool = True) -> bool:
    """
    Safely performs equality comparison for JSON-like trees.

      - strict=True (default): raises on ambiguous or unsafe comparisons
        (non-bool result, exception during __eq__, or NotImplemented).
      - strict=False: prefers structured fallbacks over repr():
          1) bool result from == if available
          2) compare tryJSONify(...) results recursively (order sensitive for
             lists/tuples and keys for dicts)
          3) last resort: repr() comparison
      
      Designed for defensive behavior against custom __eq__ implementations (e.g., Numpy, Pandas)
    """
    try:
        result = first == second

        # Standard comparison
        if isinstance(result, bool):
            return result

        if result is None:
            # Gracefully handle broken __eq__ that returns None instead of False
            return False

        if result is NotImplemented:
            raise TypeError(
                f"Equality operator not implemented between '{type(first).__name__}' and '{type(second).__name__}'"
                 " (__eq__ returned NotImplemented)."
            )

        # Non-bool result (e.g., NumPy array, Pandas Series, etc.)
        if strict:
            raise TypeError(
                f"Equality check between '{type(first).__name__}' and '{type(second).__name__}' produced unexpected"
                f" result of type '{type(result).__name__}'. Expected a boolean result. This often indicates an"
                 " overloaded __eq__ method returning an array-like object. Use deepEquals(..., strict=False) to enable"
                 " structured comparison."
            )
        
        # Structured comparison via JSON-safe coercion, then recursive compare
        firstJSON = tryJSONify(first, _maxDepth=None)
        secondJSON = tryJSONify(second, _maxDepth=None)
        return _jsonLikeEquals(firstJSON, secondJSON)

    except Exception as err:
        if strict:
            raise TypeError(
                f"Equality comparison between '{type(first).__name__}' and '{type(second).__name__}' raised"
                f" {err.__class__.__name__}: {err}."
                 " This indicates that at least one or them defines a custom __eq__ method that cannot safely compare"
                 " these types. Use deepEquals(..., strict=False) if structured comparison is acceptable."
            ) from err

        # Try structured comparison via JSON-safe coercion, then recursive compare
        try:
            firstJSON = tryJSONify(first, _maxDepth=None)
            secondJSON = tryJSONify(second, _maxDepth=None)
            return _jsonLikeEquals(firstJSON, secondJSON)
        except Exception:
            # Fallback to repr() comparison
            return repr(first) == repr(second)

def _jsonLikeEquals(first: Any, second: Any) -> bool:
    """
    Recursively performs type-aware equality for JSON-like containers produced by tryJSONify.
    Dicts: exact key set + per-key equality. Lists/tuples: positional equality.
    Scalars: Python semantics (NaN != NaN).
    """
    if first is second:
        return True
    
    # Scalars
    if isinstance(first, (type(None), bool, int, float, str)) and isinstance(second, (type(None), bool, int, float, str)):
        return first == second
    
    # Dicts
    if isinstance(first, dict) and isinstance(second, dict):
        if first.keys() != second.keys():
            return False
        return all(_jsonLikeEquals(first[key], second[key]) for key in first.keys())
    
    # Lists/tuples (tryJSONify normalizes sets/tuples to lists)
    if isinstance(first, (list, tuple)) and isinstance(second, (list, tuple)):
        if len(first) != len(second):
            return False
        return all(_jsonLikeEquals(zippedA, zippedB) for zippedA, zippedB in zip(first, second))

    # Fallback: strict equality if possible (should rarely trigger for tryJSONify outputs)
    try:
        result = (first == second)
        if isinstance(result, bool):
            return result
    except Exception:
        pass
    
    # Nah, if we end up here, just say it doesn't equal
    return False
