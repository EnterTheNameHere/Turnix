# backend/core/ops.py
from __future__ import annotations
import re
from typing import Any

__all__ = ["evaluateOp"]

"""Generic operation evaluator used in rule engines (logging filters, permission checks, etc.)."""

def evaluateOp(left: Any, op: str, right: Any) -> bool:
    """Evaluates a simple binary operation between left and right operands."""
    op = op.lower().strip()

    if op == "equals":
        return left is right or left == right
    if op == "notequals":
        return not (left is right or left == right)
    if op == "in":
        return isinstance(right, (list, set, tuple)) and left in right
    if op == "notin":
        return isinstance(right, (list, set, tuple)) and left not in right
    if op == "exists":
        return left is not None
    if op == "notexists":
        return left is None
    if op == "lt":
        return isinstance(left, (int, float)) and isinstance(right, (int, float)) and left < right
    if op == "lte":
        return isinstance(left, (int, float)) and isinstance(right, (int, float)) and left <= right
    if op == "gt":
        return isinstance(left, (int, float)) and isinstance(right, (int, float)) and left > right
    if op == "gte":
        return isinstance(left, (int, float)) and isinstance(right, (int, float)) and left >= right
    if op == "matches":
        if not isinstance(left, str) or not isinstance(right, str):
            return False
        pattern, flags = right, ""
        # ReDoS guard
        if len(pattern) > 2000:
            return False
        mm = re.fullmatch(r"/(.+)/([a-z]*)", right)
        if mm:
            pattern, flags = mm.group(1), mm.group(2)
        reFlags = 0
        if "i" in flags: reFlags |= re.IGNORECASE
        if "m" in flags: reFlags |= re.MULTILINE
        if "s" in flags: reFlags |= re.DOTALL
        try:
            return re.search(pattern, left, reFlags) is not None
        except re.error:
            return False
    return False
