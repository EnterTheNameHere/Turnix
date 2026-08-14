# file: backend/tracing/context.py ; version: 3
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, cast

from backend.core.ids import Uuid7Id
from backend.core.validation import requireInstance
from backend.tracing.errors import TraceContextError
from backend.tracing.ids import TraceEventId, TraceProducerId, TraceSpanId
from backend.tracing.validation import requireName

if TYPE_CHECKING:
    from types import TracebackType

__all__: list[str] = [
    "TraceCorrelationContext",
    "TraceCorrelationScope",
    "TraceRuntimeContext",
    "TraceSpanContext",
]


UNSET = sentinel("UNSET")


@dataclass(frozen=True, slots=True)
class TraceCorrelationContext:
    """
    Carries broad Actant and Application correlation identities.

    Correlation contexts are immutable snapshots. Each identity is optional so
    tracing can represent execution before an Actant run, application, or
    application run has been established.
    """

    actantRunId: Uuid7Id | None = None
    applicationId: Uuid7Id | None = None
    applicationRunId: Uuid7Id | None = None

    def __post_init__(self) -> None:
        """
        Validates the correlation identities.

        Raises:
            TypeError:
                If any non-None correlation identity is not a Uuid7Id.

        """
        if self.actantRunId is not None:
            requireInstance(self.actantRunId, Uuid7Id, "actantRunId")
        if self.applicationId is not None:
            requireInstance(self.applicationId, Uuid7Id, "applicationId")
        if self.applicationRunId is not None:
            requireInstance(self.applicationRunId, Uuid7Id, "applicationRunId")

    def fillMissingFrom(
        self,
        fallback: TraceCorrelationContext,
    ) -> TraceCorrelationContext:
        """
        Fills absent correlation identities from another context.

        Fields already present on this context are preserved. A field whose
        value is None is treated as missing and replaced by the corresponding
        value from fallback.

        Args:
            fallback:
                Correlation context supplying values for missing fields.

        Returns:
            A new immutable correlation context containing this context's
            existing identities supplemented by fallback values.

        Raises:
            TypeError:
                If fallback is not a TraceCorrelationContext.

        """
        cleanFallback = requireInstance(
            fallback,
            TraceCorrelationContext,
            "fallback",
        )

        return TraceCorrelationContext(
            actantRunId=(
                cleanFallback.actantRunId
                if self.actantRunId is None
                else self.actantRunId
            ),
            applicationId=(
                cleanFallback.applicationId
                if self.applicationId is None
                else self.applicationId
            ),
            applicationRunId=(
                cleanFallback.applicationRunId
                if self.applicationRunId is None
                else self.applicationRunId
            ),
        )


@dataclass(frozen=True, slots=True)
class TraceSpanContext:
    """
    Transfers immutable structural span context without lifecycle ownership.

    A span context identifies the producer and active span relationship needed
    to emit child evidence. Transferring this value does not transfer the
    lifecycle ownership of the ActiveTraceSpan that originally created it.
    """

    traceProducerId: TraceProducerId
    spanId: TraceSpanId
    spanStartEventId: TraceEventId
    spanFamily: str
    correlations: TraceCorrelationContext

    def __post_init__(self) -> None:
        """
        Validates the structural span context.

        Raises:
            TypeError:
                If an identity or correlation context has an unsupported
                runtime type, or spanFamily is not an exact built-in string.
            ValueError:
                If spanFamily is not a valid tracing name.

        """
        requireInstance(
            self.traceProducerId,
            TraceProducerId,
            "traceProducerId",
        )
        requireInstance(self.spanId, TraceSpanId, "spanId")
        requireInstance(
            self.spanStartEventId,
            TraceEventId,
            "spanStartEventId",
        )
        requireName(self.spanFamily, "spanFamily")
        requireInstance(
            self.correlations,
            TraceCorrelationContext,
            "correlations",
        )


@dataclass(slots=True)
class _ContextLease[T]:
    """
    Owns restoration of one ContextVar value installed by tracing.

    A lease may be restored exactly once and only from the thread and asyncio
    task that installed it. The ContextVar token additionally enforces Python's
    underlying execution-context ownership rules.
    """

    _contextVar: contextvars.ContextVar[T]
    _token: contextvars.Token[T]
    _ownerThreadId: int
    _ownerTask: asyncio.Task[object] | None
    _restored: bool = False

    def requireOwner(self) -> None:
        """
        Validates ownership of this active context lease.

        Raises:
            TraceContextError:
                If the lease was already restored or restoration is attempted
                from a thread or asyncio task different from its owner.

        """
        if self._restored:
            raise TraceContextError(
                "Trace context lease was already restored.",
            )

        if threading.get_ident() != self._ownerThreadId:
            raise TraceContextError(
                "Trace context lease must be restored from its owner thread.",
            )

        if _currentAsyncioTask() is not self._ownerTask:
            raise TraceContextError(
                "Trace context lease must be restored from its owner task.",
            )

    def restore(self) -> None:
        """
        Restores the exact ContextVar state replaced by this lease.

        Restoration is atomic from the lease's perspective: the lease is
        marked restored only after ContextVar.reset() succeeds. A failed reset
        therefore leaves the lease available for a valid restoration attempt.

        Raises:
            TraceContextError:
                If ownership validation fails, the lease was already restored,
                or the underlying ContextVar token cannot restore its prior
                execution-context state.

        """
        self.requireOwner()

        try:
            self._contextVar.reset(self._token)
        except Exception as err:
            raise TraceContextError(
                "Trace context lease could not restore its prior value.",
            ) from err

        self._restored = True


class TraceRuntimeContext:
    """
    Owns context-local tracing span and correlation state.

    A runtime context is independent of any individual Tracer lifecycle and
    may be shared by multiple trace producers. Ambient correlations may
    therefore be observed by multiple producers.

    Ambient span contexts remain producer-specific. A tracer may consume an
    ambient span only when that span belongs to its own trace producer; sharing
    this runtime context does not make span structure transferable between
    producers.

    Span and correlation state are stored in ContextVars so values naturally
    follow Python execution-context propagation, including asyncio context
    copying.

    No ambient span exists by default. Correlations instead use one immutable
    empty TraceCorrelationContext as the ContextVar default so execution
    contexts that do not inherit an explicitly installed correlation context
    still observe valid empty correlation state.
    """

    def __init__(self) -> None:
        """
        Initializes independent span and correlation ContextVars.

        Each runtime context owns uniquely named ContextVars. Correlations use
        an immutable default value rather than setting an initial value in the
        constructing execution context, ensuring new execution contexts such
        as independently started threads can also obtain valid empty
        correlations.
        """
        self._spanContextVar: contextvars.ContextVar[
            TraceSpanContext | None
        ] = contextvars.ContextVar(
            f"actantTraceSpanContext_{id(self)}",
            default=None,
        )

        self._correlationContextVar: contextvars.ContextVar[
            TraceCorrelationContext
        ] = contextvars.ContextVar(
            f"actantTraceCorrelationContext_{id(self)}",
            default=TraceCorrelationContext(),  # noqa: B039
        )

    def getCurrentSpan(self) -> TraceSpanContext | None:
        """
        Returns the ambient span context.

        Returns:
            The span context installed in the current execution context, or
            None when no span is ambient.

        """
        return self._spanContextVar.get()

    def getCurrentCorrelations(self) -> TraceCorrelationContext:
        """
        Returns the ambient correlation context.

        Returns:
            The correlation context installed in the current execution context,
            or the immutable empty default when none has been installed.

        """
        return self._correlationContextVar.get()

    def installSpan(
        self,
        spanContext: TraceSpanContext,
    ) -> _ContextLease[TraceSpanContext | None]:
        """
        Installs an ambient span context.

        Args:
            spanContext:
                Structural span context to make ambient for the current
                execution context.

        Returns:
            A lease that restores the exact previous span-context state.

        Raises:
            TypeError:
                If spanContext is not a TraceSpanContext.

        """
        cleanContext = requireInstance(
            spanContext,
            TraceSpanContext,
            "spanContext",
        )

        token = self._spanContextVar.set(cleanContext)

        return _ContextLease(
            _contextVar=self._spanContextVar,
            _token=token,
            _ownerThreadId=threading.get_ident(),
            _ownerTask=_currentAsyncioTask(),
        )

    def installCorrelations(
        self,
        correlations: TraceCorrelationContext,
    ) -> _ContextLease[TraceCorrelationContext]:
        """
        Installs an ambient correlation context.

        Args:
            correlations:
                Correlation context to make ambient for the current execution
                context.

        Returns:
            A lease that restores the exact previous correlation-context state.

        Raises:
            TypeError:
                If correlations is not a TraceCorrelationContext.

        """
        cleanContext = requireInstance(
            correlations,
            TraceCorrelationContext,
            "correlations",
        )

        token = self._correlationContextVar.set(cleanContext)

        return _ContextLease(
            _contextVar=self._correlationContextVar,
            _token=token,
            _ownerThreadId=threading.get_ident(),
            _ownerTask=_currentAsyncioTask(),
        )


class TraceCorrelationScope:
    """
    Installs nested Actant correlation identities for one lexical scope.

    A correlation scope modifies one TraceRuntimeContext and is independent of
    the lifecycle of any Tracer using that context. Multiple trace producers
    may therefore observe the same ambient correlations.

    Every override has three states:

    - UNSET inherits the currently ambient value.
    - None explicitly clears the currently ambient value.
    - Uuid7Id explicitly replaces the currently ambient value.

    Scope exit restores the exact prior correlation context through the
    ContextVar lease. A scope instance may be reused after successful exit but
    cannot be entered again while already active.
    """

    def __init__(
        self,
        *,
        runtimeContext: TraceRuntimeContext,
        actantRunId: Uuid7Id | UNSET | None = UNSET,
        applicationId: Uuid7Id | UNSET | None = UNSET,
        applicationRunId: Uuid7Id | UNSET | None = UNSET,
    ) -> None:
        """
        Initializes a correlation scope configuration.

        Args:
            runtimeContext:
                Runtime context whose ambient correlations the scope modifies.
                Its lifetime is independent of any Tracer using that context.
            actantRunId:
                Actant-run override. UNSET inherits the ambient value, None
                clears it, and Uuid7Id replaces it.
            applicationId:
                Application override. UNSET inherits the ambient value, None
                clears it, and Uuid7Id replaces it.
            applicationRunId:
                Application-run override. UNSET inherits the ambient value,
                None clears it, and Uuid7Id replaces it.

        Raises:
            TypeError:
                If runtimeContext is not a TraceRuntimeContext or a correlation
                override is neither UNSET, None, nor Uuid7Id.

        """
        self._runtimeContext = requireInstance(
            runtimeContext,
            TraceRuntimeContext,
            "runtimeContext",
        )
        self._actantRunId = _requireCorrelationOverride(
            actantRunId,
            "actantRunId",
        )
        self._applicationId = _requireCorrelationOverride(
            applicationId,
            "applicationId",
        )
        self._applicationRunId = _requireCorrelationOverride(
            applicationRunId,
            "applicationRunId",
        )
        self._lease: _ContextLease[TraceCorrelationContext] | None = None

    def __enter__(self) -> Self:
        """
        Activates the correlation scope.

        Returns:
            This scope instance.

        Raises:
            TraceContextError:
                If this scope is already active.

        """
        if self._lease is not None:
            raise TraceContextError(
                "Trace correlation scope is already active.",
            )

        current = self._runtimeContext.getCurrentCorrelations()

        correlations = TraceCorrelationContext(
            actantRunId=(
                current.actantRunId
                if self._actantRunId is UNSET
                else self._actantRunId
            ),
            applicationId=(
                current.applicationId
                if self._applicationId is UNSET
                else self._applicationId
            ),
            applicationRunId=(
                current.applicationRunId
                if self._applicationRunId is UNSET
                else self._applicationRunId
            ),
        )

        self._lease = self._runtimeContext.installCorrelations(correlations)

        return self

    def __exit__(
        self,
        exceptionType: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """
        Restores the prior correlations on synchronous scope exit.

        Args:
            exceptionType:
                Type of the exception leaving the scope, if any.
            exception:
                Exception leaving the scope, if any.
            traceback:
                Traceback associated with exception, if any.

        Returns:
            False so exceptions from the managed block are never suppressed.

        Raises:
            TraceContextError:
                If the scope is not active or its context lease cannot be
                restored.

        """
        self._restore()
        return False

    async def __aenter__(self) -> Self:
        """
        Activates the correlation scope for asynchronous context management.

        Returns:
            This scope instance.

        Raises:
            TraceContextError:
                If this scope is already active.

        """
        return self.__enter__()

    async def __aexit__(
        self,
        exceptionType: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """
        Restores prior correlations on asynchronous scope exit.

        Args:
            exceptionType:
                Type of the exception leaving the scope, if any.
            exception:
                Exception leaving the scope, if any.
            traceback:
                Traceback associated with exception, if any.

        Returns:
            False so exceptions from the managed block are never suppressed.

        Raises:
            TraceContextError:
                If the scope is not active or its context lease cannot be
                restored.

        """
        self._restore()
        return False

    def _restore(self) -> None:
        """
        Restores this scope's prior ambient correlation context.

        The active lease is cleared only after successful restoration so a
        failed restoration does not falsely mark the scope inactive.

        Raises:
            TraceContextError:
                If the scope is not active or restoration of its lease fails.

        """
        lease = self._lease
        if lease is None:
            raise TraceContextError("Trace correlation scope is not active.")

        lease.restore()
        self._lease = None


def _requireCorrelationOverride(
    value: Uuid7Id | UNSET | None,
    name: str,
) -> Uuid7Id | UNSET | None:
    """
    Validates one correlation-scope override.

    UNSET denotes inheritance, None denotes explicit removal, and Uuid7Id
    denotes explicit replacement.

    Args:
        value:
            Correlation override to validate.
        name:
            Diagnostic name identifying the override.

    Returns:
        The validated value unchanged.

    Raises:
        TypeError:
            If value is neither UNSET, None, nor Uuid7Id.

    """
    if value is UNSET or value is None:
        return value

    return requireInstance(
        value,
        Uuid7Id,
        name,
    )


def _currentAsyncioTask() -> asyncio.Task[object] | None:
    """
    Returns the current asyncio task when one exists.

    Returns:
        The task executing in the current thread and event loop, or None when
        called outside a running asyncio event loop.

    """
    with contextlib.suppress(RuntimeError):
        return cast(asyncio.Task[object] | None, asyncio.current_task())

    return None
