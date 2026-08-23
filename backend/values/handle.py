# file: backend/values/handle.py ; version: 3
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.core.validation import requireInstance
from backend.values.address import RelativeValueAddress, ValueAddress

if TYPE_CHECKING:
    from backend.values.layer import ValueLayer

__all__: list[str] = [
    "ValueHandle",
]


class ValueHandle:
    """
    Represents address-bound access to one Value System resolution view.

    A ValueHandle binds a ValueAddress to a particular ValueLayer. The handle
    itself does not contain or cache the addressed value. Calling load()
    resolves the address against the bound layer view at that time.

    Handle identity is therefore distinct from ValueAddress identity. Handles
    referring to the same address through different layers may observe
    different values and are not treated as equal merely because their
    addresses are equal.

    A value returned by load() is a materialized host-language value, not a
    live binding to the Value System. Mutating such an object does not by
    itself constitute Value System mutation.

    Creating a ValueHandle does not imply that a value currently exists at its
    address. load() returns MISSING when the complete bound resolution view
    does not provide a value. None remains an ordinary present value.

    Attributes:
        address:
            Canonical logical address resolved by this handle.

    """

    __slots__ = ("_layer", "address")

    def __init__(
        self,
        *,
        layer: ValueLayer,
        address: ValueAddress,
    ) -> None:
        """
        Creates a handle bound to a layer and canonical address.

        ValueHandle instances are normally created through ValueLayer.value().
        """
        # Local import avoids a circular module dependency.
        from backend.values.layer import ValueLayer  # noqa: PLC0415

        requireInstance(layer, ValueLayer, "layer")
        requireInstance(address, ValueAddress, "address")

        self._layer = layer
        self.address = address

    def load(self) -> object:
        """
        Resolves and materializes the addressed value.

        Resolution starts at the layer bound to this handle. Parent layers are
        consulted only when the current layer reports MISSING.

        Returns:
            The materialized value, including None when None is present, or
            MISSING when no layer in the resolution chain provides the
            address.

        """
        return self._layer._loadValue(self.address)

    def value(self, relative: str | RelativeValueAddress) -> ValueHandle:
        """
        Creates a handle relative to this handle's address.

        The derived handle remains bound to the same ValueLayer resolution
        view. Derivation does not load either the base address or the resulting
        address, and the base address does not need to contain a Value.

        Supplying raw string text interprets that text as a relative address in
        this context.

        For example, a handle bound to:

            npcs/alice

        deriving:

            inventory/armor

        produces a handle bound to:

            npcs/alice/inventory/armor

        Address derivation concerns Value System identity only. It does not
        access or traverse fields inside any materialized value.

        Args:
            relative:
                Canonical relative address text or an existing
                RelativeValueAddress.

        Returns:
            A ValueHandle bound to the same layer view and the resolved
            complete ValueAddress.

        Raises:
            TypeError:
                If relative is neither an exact built-in string nor a
                RelativeValueAddress.
            ValueError:
                If raw relative address text is not canonical.

        """
        return ValueHandle(
            layer=self._layer,
            address=self.address.resolve(relative),
        )

    def set(self, value: object) -> None:
        """
        Requests replacement of the addressed value through this handle's bound
        resolution view.

        A ValueHandle does not itself own mutation authority or define how
        mutation is performed. Mutation capability and semantics are determined
        by the layer view to which the handle is bound.

        A handle bound to a ValueTransaction stages the supplied value in that
        transaction. The staged value is not authoritative merely because set()
        succeeds.

        A handle bound directly to an ordinary read-only or authoritative
        layer does not bypass transaction boundaries; such a layer rejects
        direct mutation unless its concrete contract explicitly provides that
        capability.

        The supplied host-language object does not become a live Value System
        binding. A mutation-capable view is responsible for retaining the value
        according to its own representation and isolation semantics.

        Args:
            value:
                Replacement value to stage.

        Raises:
            RuntimeError:
                If the bound view does not support direct staged mutation or is
                no longer active.
            TypeError:
                If the bound view cannot accept the supplied value.

        """
        self._layer._setValue(self.address, value)
