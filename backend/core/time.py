# backend/core/time.py
from __future__ import annotations
import time

__all__ = ["nowMonotonicMs", "nowMs"]



def nowMonotonicMs() -> int:
    """
    Returns the current monotonic time in milliseconds.

    Falls back to wall-clock time if a monotonic source is unavailable.
    """
    try:
        import time as _t
        return int(_t.perf_counter() * 1000)
    except Exception:
        # Fallback: not monotonic, but ensures timestamp
        return int(time.time() * 1000)



def nowMs() -> int:
    import time as _t
    return int(_t.time() * 1000)
