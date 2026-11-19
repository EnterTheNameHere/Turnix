# file: backend/values/sentinels.py ; version: 1
from __future__ import annotations

__all__: list[str] = [
    "MISSING",
]


MISSING = sentinel("MISSING")
"""
Represents absence of a value from the relevant Value System view.

MISSING is distinct from ``None``. ``None`` is an ordinary value that may be
stored in the Value System. MISSING means that no value is present at the
relevant address according to the current resolution view.

MISSING is infrastructure state and is not itself a storable Value System
value.

Consumers compare MISSING by identity:

    value is MISSING

The Python implementation uses Python 3.15's built-in sentinel type. The
language-neutral Value System contract requires a distinct absence state but
does not require other implementations to reproduce Python's sentinel object
mechanics.
"""
