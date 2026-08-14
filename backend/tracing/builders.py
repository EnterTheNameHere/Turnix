# file: backend/tracing/builders.py ; version: 2
from __future__ import annotations

from typing import TYPE_CHECKING, Self

from backend.core.validation import (
    requireBool,
    requireExactNonBlankString,
    requireInstance,
    requireMapping,
    requireString,
)
from backend.tracing.context import TraceSpanContext
from backend.tracing.errors import TraceBuilderConsumedError, TraceContextError
from backend.tracing.validation import TraceLevel, requireName, requireOutcomeName, requireTraceLevel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.tracing.ids import TraceEventId
    from backend.tracing.references import TraceReferenceInput
    from backend.tracing.spans import ActiveTraceSpan
    from backend.tracing.tracer import Tracer
    from backend.tracing.typeDefinitions import TraceEventType, TraceSpanType

__all__: list[str] = [
    "TraceEventBuilder",
    "TraceSpanBuilder",
]


class _TraceBuilderBase:
    """
    Provides mutable construction state shared by trace builders.

    A builder is single-use. The first terminal emit() or start() attempt marks
    it consumed before control reaches Tracer materialization. Failure of that
    terminal operation therefore does not make the builder reusable.

    Parent selection has three mutually exclusive modes:

    - ambient selects the compatible span ambient at terminal operation time;
    - explicit selects one explicitly supplied TraceSpanContext;
    - origin selects parentless evidence with one explicit origin.
    """

    def __init__(
        self,
        *,
        tracer: Tracer,
    ) -> None:
        """Initializes mutable state for one trace-building attempt."""
        self._tracer = tracer
        self._domain: str | None = None
        self._level: TraceLevel | None = None
        self._message = ""
        self._label: str | None = None
        self._attributes: dict[object, object] = {}
        self._exception: BaseException | None = None
        self._exceptionAttributes: Mapping[object, object] | None = None
        self._includeExceptionStack: bool | None = None
        self._origin: str | None = None
        self._spanContext: TraceSpanContext | None = None
        self._parentMode = "ambient"
        self._causedBy: list[TraceReferenceInput] = []
        self._consumed = False

    def domain(self, domain: str) -> Self:
        """
        Sets the record-local domain override.

        An empty string explicitly selects the empty/default trace domain.
        Non-empty values must satisfy trace-name validation.

        Args:
            domain:
                Domain override to apply when the builder is consumed.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TypeError:
                If domain is not an exact built-in string.
            ValueError:
                If a non-empty domain is not a valid trace name.

        """
        self._requireUsable()
        cleanDomain = requireString(domain, "domain")
        self._domain = (
            ""
            if cleanDomain == ""
            else requireName(cleanDomain, "domain")
        )
        return self

    def level(self, level: TraceLevel) -> Self:
        """
        Sets the record-local presentation level.

        Args:
            level:
                Trace presentation level to apply when the builder is consumed.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TypeError:
                If level is not an exact built-in string.
            ValueError:
                If level is not a supported trace level.

        """
        self._requireUsable()
        self._level = requireTraceLevel(level)
        return self

    def message(self, message: str) -> Self:
        """
        Sets the human-readable record message.

        Args:
            message:
                Message to attach to the resulting trace evidence.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TypeError:
                If message is not an exact built-in string.

        """
        self._requireUsable()
        self._message = requireString(message, "message")
        return self

    def label(self, label: str) -> Self:
        """
        Sets a record-local label for the default trace type.

        This base implementation validates an ordinary trace-name label.
        TraceSpanBuilder overrides it with span-start outcome-name semantics.

        Args:
            label:
                Record-local label to apply when the builder is consumed.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TypeError:
                If label is not an exact built-in string.
            ValueError:
                If label is not a valid trace name.

        """
        self._requireUsable()
        self._label = requireName(label, "label")
        return self

    def attr(self, name: str, value: object) -> Self:
        """
        Adds or replaces one record attribute.

        Attribute values are retained as supplied and are validated and frozen
        only when the builder is consumed into immutable trace evidence.

        Args:
            name:
                Non-blank exact string attribute name.
            value:
                Attribute value to retain for terminal materialization.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TypeError:
                If name is not an exact built-in string.
            ValueError:
                If name is blank.

        """
        self._requireUsable()
        cleanName = requireExactNonBlankString(name, "name")
        self._attributes[cleanName] = value
        return self

    def attrs(self, attributes: Mapping[str, object]) -> Self:
        """
        Adds or replaces record attributes from one mapping.

        Entries are applied in mapping iteration order. Attribute values are
        retained as supplied and are validated and frozen only during terminal
        materialization.

        Args:
            attributes:
                Mapping of non-blank exact string names to attribute values.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TypeError:
                If attributes is not a mapping or an attribute name is not an
                exact built-in string.
            ValueError:
                If an attribute name is blank.

        """
        self._requireUsable()
        requireMapping(attributes, "attributes")

        for rawName, rawValue in attributes.items():
            cleanName = requireInstance(rawName, str, "attribute name")
            cleanName = requireExactNonBlankString(cleanName, "attribute name")
            self._attributes[cleanName] = rawValue

        return self

    def attachException(
        self,
        err: BaseException,
        *,
        attributes: Mapping[object, object] | None = None,
        includeStack: bool | None = None,
    ) -> Self:
        """
        Configures exception capture for the resulting trace evidence.

        The live exception object is retained by the builder. An immutable
        exception snapshot is captured only when emit() or start() performs the
        terminal tracing operation.

        Args:
            err:
                Observed exception to capture during terminal materialization.
            attributes:
                Optional catcher-owned exception attributes.
            includeStack:
                Optional per-capture stack-inclusion override.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TypeError:
                If err is not a BaseException or includeStack is not an exact
                built-in bool when supplied.

        """
        self._requireUsable()
        requireInstance(err, BaseException, "err")

        if includeStack is not None:
            requireBool(includeStack, "includeStack")

        self._exception = err
        self._exceptionAttributes = attributes
        self._includeExceptionStack = includeStack
        return self

    def origin(self, origin: str) -> Self:
        """
        Selects an explicit origin for parentless evidence.

        Selecting an origin disables ambient-parent resolution for this
        builder. An explicit span or parent cannot be selected at the same
        time.

        Args:
            origin:
                Origin name to attach to parentless evidence.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TraceContextError:
                If an explicit span context is already selected.
            TypeError:
                If origin is not an exact built-in string.
            ValueError:
                If origin is not a valid trace name.

        """
        self._requireUsable()

        if self._parentMode == "explicit":
            raise TraceContextError(
                "Explicit span context and origin must not both be supplied.",
            )

        self._origin = requireName(origin, "origin")
        self._parentMode = "origin"
        return self

    def causedBy(
        self,
        *references: TraceReferenceInput,
    ) -> Self:
        """
        Adds creator-asserted causal or logical references.

        References are retained unchanged and normalized by the Tracer during
        terminal materialization.

        Args:
            *references:
                Trace-reference inputs to append in supplied order.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.

        """
        self._requireUsable()
        self._causedBy.extend(references)
        return self

    def _setSpanContext(self, spanContext: TraceSpanContext) -> None:
        """
        Selects an explicit structural span context.

        Producer compatibility is intentionally deferred to the Tracer because
        only the owning tracer can determine whether a structurally valid
        TraceSpanContext belongs to its record producer.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TraceContextError:
                If an explicit origin is already selected.
            TypeError:
                If spanContext is not a TraceSpanContext.

        """
        self._requireUsable()

        if self._parentMode == "origin":
            raise TraceContextError(
                "Explicit span context and origin must not both be supplied.",
            )

        self._spanContext = requireInstance(
            spanContext,
            TraceSpanContext,
            "spanContext",
        )
        self._parentMode = "explicit"

    def _consume(self) -> None:
        """
        Marks this builder consumed before one terminal operation.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.

        """
        self._requireUsable()
        self._consumed = True

    def _requireUsable(self) -> None:
        """
        Requires this builder to remain available for mutation or consumption.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.

        """
        if self._consumed:
            raise TraceBuilderConsumedError(
                "Trace builder was already consumed by emit() or start().",
            )


class TraceEventBuilder(_TraceBuilderBase):
    """
    Builds and emits one ordinary trace event.

    Instances are normally created by Tracer.event(). Creating a builder does
    not reserve permission to emit: the owning Tracer must still be open when
    emit() performs its terminal operation.

    A builder is single-use. The first emit() attempt consumes it even if
    terminal validation or emission subsequently fails.
    """

    def __init__(
        self,
        *,
        tracer: Tracer,
        traceType: TraceEventType | None,
    ) -> None:
        """
        Initializes one event-building attempt.

        Builder instances are normally created by Tracer.event() rather than
        constructed directly.

        Args:
            tracer:
                Tracer that will perform terminal event materialization.
            traceType:
                Optional explicitly declared event type. None selects the
                default TRACE_EVENT semantics during emission.

        """
        super().__init__(tracer=tracer)
        self._traceType = traceType

    def span(self, spanContext: TraceSpanContext) -> Self:
        """
        Attaches the event to an explicit structural span context.

        Producer compatibility is checked by the owning Tracer when emit() is
        called.

        Args:
            spanContext:
                Structural span context to attach to the event.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TraceContextError:
                If an explicit origin is already selected.
            TypeError:
                If spanContext is not a TraceSpanContext.

        """
        self._setSpanContext(spanContext)
        return self

    def emit(self) -> TraceEventId:
        """
        Consumes the builder and emits one immutable event record.

        Consumption happens before terminal tracing begins. This builder
        therefore remains consumed if validation, context resolution,
        materialization, publication, or tracer-lifecycle admission fails.

        Returns:
            Identifier of the emitted event.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TraceClosedError:
                If the owning tracer is closing or closed.
            TraceRecursivePublicationError:
                If emission is attempted recursively from destination delivery.
            TraceContextError:
                If structural parent or origin rules are violated.
            TraceExplicitTypeOverrideError:
                If a record-local label attempts to override an explicitly
                declared event type.
            TypeError:
                If retained terminal inputs violate their runtime contracts.
            ValueError:
                If retained terminal inputs violate trace-value constraints.

        """
        self._consume()

        return self._tracer._emitEvent(
            traceType=self._traceType,
            domain=self._domain,
            level=self._level,
            message=self._message,
            label=self._label,
            attributes=self._attributes,
            exception=self._exception,
            exceptionAttributes=self._exceptionAttributes,
            includeExceptionStack=self._includeExceptionStack,
            span=self._spanContext,
            origin=self._origin,
            causedBy=tuple(self._causedBy),
            useAmbient=self._parentMode == "ambient",
        )


class TraceSpanBuilder(_TraceBuilderBase):
    """
    Builds and starts one logical trace span.

    Instances are normally created by Tracer.span(). Creating a builder does
    not reserve permission to start a span: the owning Tracer must still be
    open when start() performs its terminal operation.

    A builder is single-use. The first start() attempt consumes it even if
    terminal validation or span activation subsequently fails.
    """

    def __init__(
        self,
        *,
        tracer: Tracer,
        traceType: TraceSpanType | None,
    ) -> None:
        """
        Initializes one span-building attempt.

        Builder instances are normally created by Tracer.span() rather than
        constructed directly.

        Args:
            tracer:
                Tracer that will perform terminal span-start materialization.
            traceType:
                Optional explicitly declared span type. None selects the
                default TRACE_SPAN semantics during span start.

        """
        super().__init__(tracer=tracer)
        self._traceType = traceType

    def parent(self, spanContext: TraceSpanContext) -> Self:
        """
        Selects an explicit structural parent for the new span.

        Producer compatibility is checked by the owning Tracer when start() is
        called.

        Args:
            spanContext:
                Structural parent context for the new span.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TraceContextError:
                If an explicit origin is already selected.
            TypeError:
                If spanContext is not a TraceSpanContext.

        """
        self._setSpanContext(spanContext)
        return self

    def label(self, label: str) -> Self:
        """
        Sets a record-local start label for the default span type.

        Args:
            label:
                Span-start label to apply when this builder is consumed.

        Returns:
            This builder for fluent chaining.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TypeError:
                If label is not an exact built-in string.
            ValueError:
                If label is not a valid outcome-style tracing name.

        """
        self._requireUsable()
        self._label = requireOutcomeName(label, "label")
        return self

    def start(self) -> ActiveTraceSpan:
        """
        Consumes the builder, emits span-start evidence, and activates the
        span.

        Consumption happens before terminal tracing begins. This builder
        therefore remains consumed if validation, context resolution,
        materialization, publication, span installation, or tracer-lifecycle
        admission fails.

        Returns:
            Active lifecycle owner for the started span.

        Raises:
            TraceBuilderConsumedError:
                If this builder was already consumed.
            TraceClosedError:
                If the owning tracer is closing or closed.
            TraceRecursivePublicationError:
                If span start is attempted recursively from the destination
                delivery.
            TraceContextError:
                If structural parent or origin rules are violated.
            TraceExplicitTypeOverrideError:
                If a record-local start label attempts to override an
                explicitly declared span type.
            TypeError:
                If retained terminal inputs violate their runtime contracts.
            ValueError:
                If retained terminal inputs violate trace-value constraints.

        """
        self._consume()

        return self._tracer._startSpan(
            traceType=self._traceType,
            domain=self._domain,
            level=self._level,
            message=self._message,
            label=self._label,
            attributes=self._attributes,
            exception=self._exception,
            exceptionAttributes=self._exceptionAttributes,
            includeExceptionStack=self._includeExceptionStack,
            parent=self._spanContext,
            origin=self._origin,
            causedBy=tuple(self._causedBy),
            useAmbient=self._parentMode == "ambient",
        )
