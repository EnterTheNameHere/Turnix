# file: backend/values/layer.py ; version: 2
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING

from backend.core.validation import (
    requireOptionalInstance,
    typeName,
)
from backend.values.address import ValueAddress
from backend.values.sentinels import MISSING

if TYPE_CHECKING:
    from backend.values.handle import ValueHandle
    from backend.values.transaction import ValueTransaction

__all__: list[str] = [
    "InMemoryValueLayer",
    "ValueLayer",
]


class ValueLayer(ABC):
    """
    Defines one resolution layer in the Actant Value System.

    A ValueLayer contributes values to one logical Value System view. A layer
    may have at most one parent. Resolution first asks the current layer for
    the addressed value and consults its parent only when the current layer
    reports MISSING.

    Parent resolution is fallback, not merging. If a local value exists, that
    value is the complete result for the address regardless of whether the
    parent also contains a value there.

    None is an ordinary present value and therefore shadows a parent value.
    Only MISSING causes parent fallback.

    The one-parent model is deliberately generic. Composition of multiple
    independent sources is a domain decision and is not implicitly performed
    by ValueLayer.

    A ValueLayer represents a resolution view, not necessarily a physical
    storage object. Concrete layers may materialize values from memory, files,
    databases, remote services, Pack assets, transaction overlays, or other
    providers.

    Direct mutation is not implied by being a ValueLayer. Mutation seeking
    Value System transaction guarantees is staged through a ValueTransaction.
    A concrete layer may internally accept a committed promotion without
    exposing ordinary direct mutation to callers.

    Args:
        parent:
            Optional parent used for fallback when this layer does not provide
            an addressed value locally.

    """

    __slots__ = ("_parent",)

    def __init__(
        self,
        *,
        parent: ValueLayer | None = None,
    ) -> None:
        requireOptionalInstance(parent, ValueLayer, "parent")
        self._parent = parent

    def value(self, address: str | ValueAddress) -> ValueHandle:
        """
        Creates a handle bound to an address in this layer's resolution view.

        Supplying raw string text constructs and validates a ValueAddress.
        Supplying an existing ValueAddress preserves that identity object.

        Creating a handle does not load or materialize the addressed value.
        The handle remains bound to this particular layer view and resolves
        through that view when load() is called.

        Args:
            address:
                Canonical address text or an existing ValueAddress.

        Returns:
            A ValueHandle bound to this layer and address.

        Raises:
            TypeError:
                If address is neither an exact built-in string nor a
                ValueAddress.
            ValueError:
                If raw address text is not canonical ValueAddress syntax.

        """
        # Local import avoids a circular module dependency.
        from backend.values.handle import ValueHandle  # noqa: PLC0415

        return ValueHandle(
            layer=self,
            address=self._coerceAddress(address),
        )

    def openTransaction(self) -> ValueTransaction:
        """
        Creates a transaction whose parent resolution view is this layer.

        The returned transaction initially contains no local values. Reads
        therefore fall through to this layer until values are staged in the
        transaction.

        Creating a transaction does not mutate this layer. Successful outer
        transaction commit promotes the transaction's staged values through
        this layer's internal commit boundary.

        Returns:
            A new active ValueTransaction parented by this layer.

        """
        # Local import avoids a circular module dependency.
        from backend.values.transaction import ValueTransaction  # noqa: PLC0415

        return ValueTransaction(parent=self)

    def _loadValue(self, address: ValueAddress) -> object:
        """
        Resolves an addressed value through this layer and its parent chain.

        Concrete providers implement only local lookup. This method owns the
        generic parent-fallback rule so individual providers cannot
        accidentally implement different resolution semantics.
        """
        localValue = self._loadLocalValue(address)

        if localValue is not MISSING:
            return localValue

        if self._parent is None:
            return MISSING

        return self._parent._loadValue(address)

    def _setValue(self, address: ValueAddress, value: object) -> None:
        """
        Attempts to mutate a value through this resolution view.

        Ordinary ValueLayer instances do not expose direct mutation.
        Transaction layers override this method to stage mutation.

        Raises:
            RuntimeError:
                If this layer does not support direct staged mutation.

        """
        raise RuntimeError(
            f"{type(self).__qualname__} does not support direct value "
            "mutation; use a ValueTransaction.",
        )

    def _acceptPromotion(self, values: Mapping[ValueAddress, object]) -> None:
        """
        Accepts a batch submitted for promotion by a committing child.

        This is an internal mutation boundary, not a public direct-write API.
        Concrete authoritative layers that can receive outer transaction
        commits override it. Transaction layers override it to receive child
        transaction promotion into their own staged state.

        Implementations must either accept the complete supplied batch or
        leave their prior visible state unchanged.

        Raises:
            RuntimeError:
                If this layer cannot accept promoted mutation.

        """
        raise RuntimeError(
            f"{type(self).__qualname__} does not accept committed value "
            "promotion.",
        )

    @abstractmethod
    def _loadLocalValue(self, address: ValueAddress) -> object:
        """
        Loads an addressed value from this layer only.

        Returns MISSING when the layer has no local value at the address.
        Returning None means that None is locally present.

        A returned materialized value must be safe to expose to the caller
        according to the concrete provider's representation semantics.
        Implementations must not expose mutable authoritative backing state
        merely by returning it from this method.
        """

    @staticmethod
    def _coerceAddress(address: str | ValueAddress) -> ValueAddress:
        """Returns address as a validated ValueAddress instance."""
        if isinstance(address, ValueAddress):
            return address

        if type(address) is str:
            return ValueAddress(address)

        raise TypeError(
            "address must be a ValueAddress or exact built-in string; "
            f"received {typeName(address)}.",
        )


class InMemoryValueLayer(ValueLayer):
    """
    Provides Value System values from in-process Python memory.

    InMemoryValueLayer is a concrete testing and runtime provider. It is not
    the definition of Value System storage semantics.

    Values supplied during construction are snapshotted before being retained.
    Values loaded from the layer are snapshotted again before being returned.
    Consequently, mutating either the original constructor input or a loaded
    mutable Python object does not mutate the layer's retained value.

    Successful outer transaction commit may promote a batch of values into
    this layer. The complete incoming batch is snapshotted before any retained
    value is replaced. If snapshotting any incoming value fails, the retained
    layer remains unchanged.

    This implementation uses copy.deepcopy() to provide detached snapshots.
    Deep copying is a Python-specific implementation strategy and is not a
    language-neutral Value System requirement. Other providers may satisfy
    detached materialization semantics through decoding, immutable
    representations, database reads, remote materialization, or other means.

    MISSING is reserved for Value System absence and cannot be stored as a
    value. None is an ordinary storable value.

    Args:
        values:
            Optional initial mapping from canonical address text to values.
        parent:
            Optional parent layer used when an address is locally absent.

    Raises:
        TypeError:
            If values is not a mapping, an address key is invalid, MISSING is
            supplied as a value, or a supplied value cannot be snapshotted by
            this provider.
        ValueError:
            If an address key is not canonical ValueAddress syntax.

    """

    __slots__ = ("_values",)

    def __init__(
        self,
        *,
        values: Mapping[str, object] | None = None,
        parent: ValueLayer | None = None,
    ) -> None:
        super().__init__(parent=parent)

        requireOptionalInstance(values, Mapping, "values")

        if values is None:
            self._values: dict[ValueAddress, object] = {}
            return

        retainedValues: dict[ValueAddress, object] = {}

        for addressText, value in values.items():
            if type(addressText) is not str:
                raise TypeError(
                    "value address key must be an exact built-in string; "
                    f"received {typeName(addressText)}.",
                )

            address = ValueAddress(addressText)
            retainedValues[address] = self._snapshotValue(value)

        self._values = retainedValues

    def _loadLocalValue(self, address: ValueAddress) -> object:
        storedValue = self._values.get(address, MISSING)

        if storedValue is MISSING:
            return MISSING

        return self._snapshotValue(storedValue)

    def _acceptPromotion(self, values: Mapping[ValueAddress, object]) -> None:
        """
        Commits one complete promoted batch into this in-memory layer.

        Every incoming value is snapshotted before any retained value is
        changed. Failure to snapshot one value therefore prevents partial
        application of the promoted batch.
        """
        promotedValues = {
            address: self._snapshotValue(value)
            for address, value in values.items()
        }

        self._values.update(promotedValues)

    @staticmethod
    def _snapshotValue(value: object) -> object:
        """
        Creates this provider's detached snapshot of a materialized value.

        MISSING is reserved infrastructure state and cannot be retained as a
        Value System value.
        """
        if value is MISSING:
            raise TypeError(
                "MISSING represents Value System absence and cannot be stored "
                "as a value.",
            )

        try:
            return deepcopy(value)
        except Exception as err:
            raise TypeError(
                "value cannot be snapshotted by InMemoryValueLayer; "
                f"received {typeName(value)}.",
            ) from err
