# backend/core/errors.py
from __future__ import annotations



class ReactorScramError(Exception):
    """Raised when Turnix violates a core invariant and hit the shutdown button."""
    pass
