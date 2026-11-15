# file: backend/llm/streamObservation.py ; version: 1
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class StreamOutcome(StrEnum):
    """Describes only the observed provider span, never a surrounding Job or transaction."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class StreamObservationConfiguration:
    """Bounds fixed monotonic delivery windows, including the current partial window."""

    windowSeconds: float = 1.0
    historyWindows: int = 120

    def __post_init__(self) -> None:
        """Rejects cadences that cannot form finite positive nanosecond windows."""
        if type(self.windowSeconds) not in {int, float}:
            raise ValueError("windowSeconds must be finite and positive.")
        try:
            duration = self.windowSeconds * 1e9
        except OverflowError:
            raise ValueError("windowSeconds must be finite and positive.") from None
        if not math.isfinite(duration) or duration < 1:
            raise ValueError("windowSeconds must be finite and represent at least one nanosecond.")
        if type(self.historyWindows) is not int or self.historyWindows <= 0:
            raise ValueError("historyWindows must be a positive exact integer.")


@dataclass(frozen=True, slots=True)
class StreamObservationWindow:
    """Immutable delivery statistics for a half-open host-monotonic window.

    A partial live/terminal window ends at actual observation time. Character counts
    are Unicode code points, not bytes, graphemes or model tokens. Gap statistics
    belong to arrivals in this window and include the preceding arrival even if it
    was outside the window. Empty windows expose delivery silence explicitly.
    """

    startNs: int
    endNs: int
    characterCount: int = 0
    deliveryCount: int = 0
    firstTextNs: int | None = None
    lastTextNs: int | None = None
    gapCount: int = 0
    gapSumNs: int = 0
    minGapNs: int | None = None
    maxGapNs: int | None = None
    partial: bool = False

    @property
    def charactersPerSecond(self) -> float | None:
        """Returns observed delivery throughput, unavailable for zero elapsed time."""
        return self.characterCount * 1e9 / (self.endNs - self.startNs) if self.endNs > self.startNs else None

    @property
    def meanGapNs(self) -> float | None:
        """Returns mean inter-arrival spacing, unavailable before any successive delivery."""
        return self.gapSumNs / self.gapCount if self.gapCount else None


@dataclass(frozen=True, slots=True)
class StreamObservationSnapshot:
    """Bounded disposable stream evidence containing no text, requests or provider metadata.

    Origin is immediately before provider stream entry, after profile/query preparation.
    Arrival is validated text acceptance before the existing stream observer callback.
    This measures host delivery, including iteration/processing and previous callback
    delays, rather than engine compute latency. Terminal time includes stream
    validation and result assembly, before returning to processing/completion work.
    Native-token evidence remains separate
    in the engine-owned StreamingLlmResult.providerMetadata; no token rate is inferred.
    """

    originNs: int | None
    observedThroughNs: int | None
    endNs: int | None
    firstTextNs: int | None
    lastTextNs: int | None
    characterCount: int
    deliveryCount: int
    windows: tuple[StreamObservationWindow, ...]
    droppedWindowCount: int
    outcome: StreamOutcome | None
    active: bool
    released: bool
    unavailableReason: str | None

    @property
    def firstTextLatencyNs(self) -> int | None:
        """Returns first non-empty delivery latency from the explicit provider-entry origin."""
        return self.firstTextNs - self.originNs if self.firstTextNs is not None else None

    @property
    def silenceNs(self) -> int | None:
        """Returns elapsed silence since the last text, or since origin before first text."""
        if self.observedThroughNs is None:
            return None
        previous = self.lastTextNs if self.lastTextNs is not None else self.originNs
        return self.observedThroughNs - previous


class StreamObservation:
    """Single-run observation handle with explicit release and no background work.

    Pass to pipeline run()/runProcessing() for opt-in collection. A run claim ends on
    every provider-span exit; bounded summaries remain readable until release().
    Live snapshot() advances windows to the current clock, so silence is visible
    without a polling task. Snapshots and delivery/release serialize under one lock.
    No invocation context, provider or generated content is retained.
    """

    def __init__(
        self,
        configuration: StreamObservationConfiguration | None = None,
        *,
        monotonicNs: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        """Creates an inactive handle without reading the clock or installing an observer."""
        self._configuration = configuration or StreamObservationConfiguration()
        self._windowNs = int(self.configuration.windowSeconds * 1e9)
        self._clock = monotonicNs
        self._lane = RLock()
        self._windows: deque[StreamObservationWindow] = deque(maxlen=self.configuration.historyWindows)
        self._origin: int | None = None
        self._through: int | None = None
        self._end: int | None = None
        self._first: int | None = None
        self._last: int | None = None
        self._characters = 0
        self._deliveries = 0
        self._windowIndex = 0
        self._current: StreamObservationWindow | None = None
        self._outcome: StreamOutcome | None = None
        self._active = False
        self._begun = False
        self._released = False
        self._unavailable: str | None = None

    @property
    def configuration(self) -> StreamObservationConfiguration:
        """Returns immutable lifetime configuration; retention/cadence cannot change mid-run."""
        return self._configuration

    def begin(self) -> StreamObservationClaim | None:
        """Acquires this handle once; reused or explicitly released handles are inert.

        Pipeline calls this immediately before provider entry. Clock failure marks
        observation unavailable while still returning a claim that can be finalized.
        """
        with self._lane:
            if self._begun or self._released:
                return None
            self._begun = True
            self._active = True
            claim = StreamObservationClaim(self)
            try:
                now = self._now()
                self._origin = self._through = now
                self._current = StreamObservationWindow(now, now, partial=True)
            except Exception as err:  # noqa: BLE001 - optional observation cannot affect inference.
                self._fail(err)
            return claim

    def snapshot(self) -> StreamObservationSnapshot:
        """Returns immutable bounded evidence, lazily materializing silence while active."""
        with self._lane:
            if self._active and self._unavailable is None:
                try:
                    self._advance(self._now())
                except Exception as err:  # noqa: BLE001 - clock failures are observation unavailability.
                    self._fail(err)
            windows = tuple(self._windows)
            if self._current is not None and (
                self._current.endNs > self._current.startNs or self._current.deliveryCount
            ):
                windows = (*windows, self._current)
            windows = windows[-self.configuration.historyWindows:]
            totalWindows = self._windowIndex + (1 if self._current is not None and self._current in windows else 0)
            return StreamObservationSnapshot(
                originNs=self._origin, observedThroughNs=self._through, endNs=self._end,
                firstTextNs=self._first, lastTextNs=self._last,
                characterCount=self._characters, deliveryCount=self._deliveries,
                windows=windows, droppedWindowCount=max(0, totalWindows - len(windows)),
                outcome=self._outcome, active=self._active, released=self._released,
                unavailableReason=self._unavailable,
            )

    def release(self) -> None:
        """Revokes observation and clears owned summaries without cancelling provider execution."""
        with self._lane:
            self._released = True
            self._active = False
            if self._outcome is None:
                self._outcome = StreamOutcome.CLOSED
            self._windows.clear()
            self._current = None
            self._first = self._last = None
            self._characters = self._deliveries = self._windowIndex = 0

    def _now(self) -> int:
        """Requires exact non-negative monotonic nanoseconds without clock regression."""
        now = self._clock()
        if type(now) is not int or now < 0 or (self._through is not None and now < self._through):
            raise ValueError("Observation clock must provide monotonic non-negative integer nanoseconds.")
        return now

    def _fail(self, err: Exception) -> None:
        """Records bounded error type evidence, retaining no exception, message or traceback."""
        self._unavailable = type(err).__qualname__

    def _advance(self, now: int) -> None:
        """Advances fixed windows in O(retention), even across arbitrarily long silent gaps."""
        target = (now - self._origin) // self._windowNs
        if target > self._windowIndex:
            self._windows.append(self._window(self._current.startNs + self._windowNs, partial=False))
            firstEmpty = max(self._windowIndex + 1, target - self.configuration.historyWindows)
            for index in range(firstEmpty, target):
                start = self._origin + index * self._windowNs
                self._windows.append(StreamObservationWindow(start, start + self._windowNs))
            self._windowIndex = target
            start = self._origin + target * self._windowNs
            self._current = StreamObservationWindow(start, now, partial=True)
        else:
            self._current = self._window(now, partial=True)
        self._through = now

    def _window(self, end: int, *, partial: bool) -> StreamObservationWindow:
        """Projects the current accumulator with the actual elapsed endpoint."""
        current = self._current
        return StreamObservationWindow(
            startNs=current.startNs, endNs=end,
            characterCount=current.characterCount, deliveryCount=current.deliveryCount,
            firstTextNs=current.firstTextNs, lastTextNs=current.lastTextNs,
            gapCount=current.gapCount, gapSumNs=current.gapSumNs,
            minGapNs=current.minGapNs, maxGapNs=current.maxGapNs, partial=partial,
        )

    def _record(self, text: str) -> None:
        """Counts a non-empty delivery and discards its content before returning."""
        now = self._now()
        self._advance(now)
        count = len(text)
        gap = now - self._last if self._last is not None else None
        current = self._current
        self._current = StreamObservationWindow(
            startNs=current.startNs, endNs=now,
            characterCount=current.characterCount + count, deliveryCount=current.deliveryCount + 1,
            firstTextNs=current.firstTextNs if current.firstTextNs is not None else now,
            lastTextNs=now, gapCount=current.gapCount + (gap is not None),
            gapSumNs=current.gapSumNs + (gap if gap is not None else 0),
            minGapNs=min(current.minGapNs, gap) if current.minGapNs is not None else gap,
            maxGapNs=max(current.maxGapNs, gap) if current.maxGapNs is not None else gap,
            partial=True,
        )
        if self._first is None:
            self._first = now
        self._last = now
        self._characters += count
        self._deliveries += 1


class StreamObservationClaim:
    """Run-owned recording seam that isolates all ordinary optional measurement failures."""

    def __init__(self, observation: StreamObservation) -> None:
        """Binds the single-run handle without retaining any invocation or provider object."""
        self._observation = observation
        self._finished = False

    def recordText(self, text: str) -> None:
        """Observes one accepted non-empty text fragment; ignores revoked/unavailable claims."""
        observation = self._observation
        with observation._lane:
            if not text or not observation._active or observation._unavailable is not None:
                return
            try:
                observation._record(text)
            except Exception as err:  # noqa: BLE001 - recording is non-authoritative and optional.
                observation._fail(err)

    def fail(self, err: Exception) -> None:
        """Disables further measurement after an unexpected seam failure without retaining its payload."""
        with self._observation._lane:
            self._observation._fail(err)

    def finish(self, outcome: StreamOutcome) -> None:
        """Releases this run claim exactly once, retaining only bounded terminal evidence."""
        observation = self._observation
        with observation._lane:
            if self._finished:
                return
            self._finished = True
            if observation._released:
                return
            try:
                if observation._unavailable is None:
                    end = observation._now()
                    observation._advance(end)
                    observation._end = end
            except Exception as err:  # noqa: BLE001 - final clock failures cannot mask execution outcomes.
                observation._fail(err)
            finally:
                observation._active = False
                observation._outcome = outcome
