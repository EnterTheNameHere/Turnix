# backend/core/logging/filters.py
from __future__ import annotations
import logging
import time
import threading
from collections import deque, defaultdict
from collections.abc import Callable

from backend.core.redaction import redactText

__all__ = ["SamplingFilter", "RecurringSuppressFilter"]

# Upper bound for normalized
MAX_KEY_LEN = 512



class SamplingFilter(logging.Filter):
    """Pass only every Nth record (attach to high-volume loggers)."""
    def __init__(self, sampleEvery: int = 10):
        super().__init__()
        self.sampleEvery = max(1, int(sampleEvery))
        self._count = 0

    def filter(self, record: logging.LogRecord) -> bool:
        self._count += 1
        return (self._count % self.sampleEvery) == 0



class RecurringSuppressFilter(logging.Filter):
    """
    Suppresses recurring identical log messages after `maxPerWindow` occurrences
    within a sliding `windowSeconds`. Emits a summary once the window slides and
    logging resumes for that key.

    Key = (logger name, levelno, normalized message)

    Parameters:
      - windowSeconds: length of the sliding window (default: 60)
      - maxPerWindow: allow up to N messages per window before suppressing (default: 5)
      - summaryLevel: level for suppression summaries (default: INFO)
      - normalize: callable to normalize record into a key-friendly text
                   default is redactText(record.getMessage()) stripped and squashed
    
    Thread-safe and lightweight; uses per-key timestamp deques.
    """
    def __init__(
            self,
            *,
            windowSeconds: int = 60,
            maxPerWindow: int = 5,
            summaryLevel: int = logging.INFO,
            normalize: Callable[[logging.LogRecord], str] | None = None,
    ):
        super().__init__()
        self.windowSeconds = max(1, int(windowSeconds))
        self.maxPerWindow = max(1, int(maxPerWindow))
        self.summaryLevel = int(summaryLevel)
        self.normalize = normalize or self._defaultNormalize

        # Per-key sliding window of timestamps
        self._buckets: dict[tuple[str, int, str], deque[float]] = defaultdict(deque)
        # Suppressed count not yet reported
        self._suppressedCounts: dict[tuple[str, int, str], int] = defaultdict(int)
        self._lock = threading.Lock()
    
    def _defaultNormalize(self, record: logging.LogRecord) -> str:
        # Use fully formatted message, redacted, with whitespace squashed
        try:
            msg = redactText(record.getMessage())
        except Exception:
            msg = str(record.msg)
        norm = " ".join(str(msg).split())
        if len(norm) > MAX_KEY_LEN:
            norm = norm[:MAX_KEY_LEN] + "…"
        return norm
    
    def _keyOf(self, record: logging.LogRecord) -> tuple[str, int, str]:
        return (record.name, record.levelno, self.normalize(record))
    
    def _pruneOld(self, dq: deque[float], now: float) -> None:
        limit = now - self.windowSeconds
        while dq and dq[0] < limit:
            dq.popleft()
        
    def _emitSummary(self, key: tuple[str, int, str]) -> None:
        suppressedCount = self._suppressedCounts.get(key, 0)
        if suppressedCount <= 0:
            return
        loggerName, _levelno, normMessage = key
        # Emit on the same logger to keep discoverability
        lg = logging.getLogger(loggerName)
        try:
            # Mark summary so it won't be suppressed by this filter again
            lg.log(
                self.summaryLevel,
                "Suppressed %d repeated logs: %s",
                suppressedCount,
                normMessage,
                extra={"_noRecurringSuppress": True}
            )
        except Exception:
            # Never crash logging due to summary emission
            pass
        finally:
            self._suppressedCounts[key] = 0
    
    def _maybeCleanup(self):
        """Cleanup old entries from the buckets."""
        if len(self._buckets) > 5000:
            # Drop stale keys with empty windows and zero suppressed count
            for key in list(self._buckets.keys())[:2000]:
                if not self._buckets[key] and not self._suppressedCounts.get(key, 0):
                    self._buckets.pop(key, None)
                    self._suppressedCounts.pop(key, None)

    def filter(self, record: logging.LogRecord) -> bool:
        # Allow summaries to pass through
        if getattr(record, "_noRecurringSuppress", False):
            return True
        
        now = time.monotonic()
        key = self._keyOf(record)

        with self._lock:
            self._maybeCleanup()
            dq = self._buckets[key]
            self._pruneOld(dq, now)

            if len(dq) < self.maxPerWindow:
                # Allow this record, track it, and if previously suppressed, emit summary now
                dq.append(now)
                if self._suppressedCounts.get(key, 0) > 0:
                    # We are exiting suppression window; report what we dropped
                    self._emitSummary(key)
                return True
            
            # Over the limit -> suppress and count
            self._suppressedCounts[key] += 1
            # Still track time to keep window sliding properly
            dq.append(now)
            # Do not log this record
            return False
                    