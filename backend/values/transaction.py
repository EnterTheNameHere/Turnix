# file: backend/values/transaction.py ; version: 1
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Literal

from backend.core.validation import typeName
from backend.values.layer import ValueLayer
from backend.values.sentinels import MISSING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.values.address import ValueAddress

__all__: list[str] = [
    "ValueTransaction",
]


type _TransactionState = Literal[
    "active",
    "committed",
    "aborted",
]


class ValueTransaction(ValueLayer):
    """
    Represents one speculative mutation layer in the Actant Value System.

    A ValueTransaction is a ValueLayer whose local values are staged mutation.
    Reads resolve staged values first and otherwise continue through the
    ordinary parent-layer resolution rules.

    Mutation through a handle bound to this transaction stages a replacement
    value in this transaction only. Staging does not mutate the parent and
    does not make the value authoritative.

    Transactions may be nested. A child transaction's successful commit
    promotes its staged values into its immediate parent transaction. Such a
    promotion remains staged and does not become authoritative merely because
    the child committed.

    A child transaction commits by promoting its staged values into its
    immediate parent transaction. A transaction whose immediate parent is a
    non-transaction ValueLayer submits its staged batch to that layer's
    promotion boundary. The parent layer determines whether it can accept the
    batch and what successful acceptance means for that layer.

    When that parent is the authoritative state layer, successful promotion is
    the point at which the transaction crosses the authoritative commit
    boundary.

    abort() discards this transaction's staged values. It does not undo
    already-authoritative state and is therefore intentionally distinct from
    the term "rollback".

    A transaction is active until commit() or abort() succeeds. Committed and
    aborted transactions are terminal and cannot subsequently load, stage,
    commit, abort, or create child transactions.

    A transaction cannot commit or abort while it has unresolved active child
    transactions. Child lifecycle must therefore be resolved explicitly
    before the parent transaction itself is resolved.

    Values staged by this Python implementation are detached snapshots.
    copy.deepcopy() is used as the current in-memory staging mechanism and is
    not a language-neutral requirement of the Value System.

    Args:
        parent:
            Immediate parent layer into which successful commit promotes this
            transaction's staged values.

    """

    __slots__ = (
        "_activeChildren",
        "_state",
        "_values",
    )

    def __init__(
        self,
        *,
        parent: ValueLayer,
    ) -> None:
        super().__init__(parent=parent)

        self._values: dict[ValueAddress, object] = {}
        self._state: _TransactionState = "active"
        self._activeChildren: set[ValueTransaction] = set()

        if isinstance(parent, ValueTransaction):
            parent._registerChild(self)

    def openTransaction(self) -> ValueTransaction:
        """
        Creates an active child transaction parented by this transaction.

        The child initially sees this transaction's staged values through
        ordinary parent fallback. Successful child commit promotes only into
        this transaction and does not directly mutate any authoritative
        ancestor.

        Returns:
            A new active child ValueTransaction.

        Raises:
            RuntimeError:
                If this transaction is no longer active.

        """
        self._requireActive()
        return ValueTransaction(parent=self)

    def commit(self) -> None:
        """
        Promotes all staged values into this transaction's immediate parent.

        Child transaction commit promotes into the parent transaction's staged
        state. Outer transaction commit may cross into an authoritative layer
        if that parent accepts committed promotion.

        Promotion is requested as one batch. If the parent rejects or fails
        the promotion, this transaction remains active with its staged values
        intact so its owner may inspect, retry, or abort it.

        Raises:
            RuntimeError:
                If this transaction is no longer active, has unresolved active
                children, or its parent rejects promotion.
            TypeError:
                If the parent cannot materialize a promoted value according to
                its provider-specific representation rules.

        """
        self._requireActive()
        self._requireNoActiveChildren()

        parent = self._requireParent()
        parent._acceptPromotion(self._values)

        self._values.clear()
        self._state = "committed"
        self._notifyParentResolved()

    def abort(self) -> None:
        """
        Discards all mutation staged in this transaction.

        Aborting a child transaction does not mutate its parent. Aborting a
        parent transaction also discards values previously promoted into that
        parent by successfully committed children.

        A transaction with unresolved active children cannot be aborted. The
        children must first commit or abort explicitly.

        Raises:
            RuntimeError:
                If this transaction is no longer active or has unresolved
                active children.

        """
        self._requireActive()
        self._requireNoActiveChildren()

        self._values.clear()
        self._state = "aborted"
        self._notifyParentResolved()

    def _loadLocalValue(self, address: ValueAddress) -> object:
        """Loads one staged local value from this active transaction."""
        self._requireActive()

        storedValue = self._values.get(address, MISSING)

        if storedValue is MISSING:
            return MISSING

        return self._snapshotValue(storedValue)

    def _setValue(self, address: ValueAddress, value: object) -> None:
        """Stages one detached replacement value in this transaction."""
        self._requireActive()
        self._values[address] = self._snapshotValue(value)

    def _acceptPromotion(self, values: Mapping[ValueAddress, object]) -> None:
        """
        Accepts one complete child-transaction promotion into staged state.

        The complete incoming batch is snapshotted before any existing staged
        value is replaced. Failure therefore leaves this transaction's prior
        staged state unchanged.
        """
        self._requireActive()

        promotedValues = {
            address: self._snapshotValue(value)
            for address, value in values.items()
        }

        self._values.update(promotedValues)

    def _registerChild(self, child: ValueTransaction) -> None:
        """Registers one newly created active child transaction."""
        self._requireActive()
        self._activeChildren.add(child)

    def _notifyParentResolved(self) -> None:
        """Removes this resolved transaction from its parent's active children."""
        parent = self._parent

        if isinstance(parent, ValueTransaction):
            parent._activeChildren.discard(self)

    def _requireParent(self) -> ValueLayer:
        """Returns this transaction's required immediate parent."""
        parent = self._parent

        if parent is None:
            raise RuntimeError("ValueTransaction has no parent layer.")

        return parent

    def _requireActive(self) -> None:
        """Raises if this transaction has already reached a terminal state."""
        if self._state == "active":
            return

        raise RuntimeError(
            f"ValueTransaction is already {self._state}.",
        )

    def _requireNoActiveChildren(self) -> None:
        """Raises while unresolved active child transactions remain."""
        if not self._activeChildren:
            return

        raise RuntimeError(
            "ValueTransaction cannot be resolved while active child "
            f"transactions remain; received {len(self._activeChildren)} "
            "active child transaction(s).",
        )

    @staticmethod
    def _snapshotValue(value: object) -> object:
        """
        Creates a detached snapshot for transaction-local staged state.

        MISSING represents absence and cannot itself be staged as a value.
        """
        if value is MISSING:
            raise TypeError(
                "MISSING represents Value System absence and cannot be staged "
                "as a value.",
            )

        try:
            return deepcopy(value)
        except Exception as err:
            raise TypeError(
                "value cannot be snapshotted by ValueTransaction; "
                f"received {typeName(value)}.",
            ) from err
