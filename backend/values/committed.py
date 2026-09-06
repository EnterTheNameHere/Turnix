# file: backend/values/committed.py ; version: 6
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock

from backend.core.errors import ActantError
from backend.values.address import ValueAddress
from backend.values.layer import ValueLayer
from backend.values.payload import ChunkValueRef, InMemoryChunkStore, ValueRef, decodeJsonValue, encodeJsonValue
from backend.values.sentinels import MISSING

__all__ = ["CommittedValueLayer", "CommittedValueTransaction", "StateConflictError"]


class StateConflictError(ActantError, RuntimeError):
    """Raised when authoritative state changed after a transaction first touched a key."""


@dataclass(frozen=True, slots=True)
class _CommittedRevision:
    revisionId: int
    valueRef: ValueRef


class CommittedValueLayer(ValueLayer):
    """Authoritative revisioned Value System layer backed by immutable ValueRefs.

    This is the single authoritative mutable Value System provider. Generic
    ValueLayer implementations may contribute read-only resolution views, but
    transaction creation, revision advancement, and conflict detection belong
    here so authoritative mutation has one project-wide semantic contract.

    CommittedValueLayer also participates in the normal ValueHandle API. Code
    may therefore use layer.value(address) for address-bound reads while
    existing runtime code may continue to use load(address) directly.
    """

    def __init__(self, *, chunkStore: InMemoryChunkStore | None = None) -> None:
        super().__init__(parent=None)
        self.chunkStore = chunkStore or InMemoryChunkStore()
        self._values: dict[ValueAddress, _CommittedRevision] = {}
        self._lock = RLock()

    def load(self, address: str | ValueAddress) -> object:
        """Materializes the current authoritative value at address."""
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        return self._loadLocalValue(key)

    def _loadLocalValue(self, address: ValueAddress) -> object:
        """Loads one authoritative local value for ValueLayer resolution."""
        with self._lock:
            revision = self._values.get(address)
        if revision is None:
            return MISSING
        return decodeJsonValue(revision.valueRef, store=self.chunkStore)

    def revisionId(self, address: str | ValueAddress) -> int:
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        with self._lock:
            revision = self._values.get(key)
            return 0 if revision is None else revision.revisionId

    def openTransaction(self) -> "CommittedValueTransaction":
        return CommittedValueTransaction(root=self, parent=None)

    def _commit(self, staged: dict[ValueAddress, object], bases: dict[ValueAddress, int]) -> None:
        temporaryStore = InMemoryChunkStore()
        encoded = {address: encodeJsonValue(value, store=temporaryStore) for address, value in staged.items()}

        with self._lock:
            for address, baseRevisionId in bases.items():
                currentRevision = self._values.get(address)
                current = 0 if currentRevision is None else currentRevision.revisionId
                if current != baseRevisionId:
                    raise StateConflictError(
                        f"State conflict at {address}: transaction observed revision {baseRevisionId}, current revision is {current}.",
                    )

            for valueRef in encoded.values():
                if isinstance(valueRef, ChunkValueRef):
                    self.chunkStore.put(temporaryStore.require(valueRef.chunkId))

            replacements: dict[ValueAddress, _CommittedRevision] = {}
            for address, valueRef in encoded.items():
                current = self._values.get(address)
                nextRevision = 1 if current is None else current.revisionId + 1
                replacements[address] = _CommittedRevision(revisionId=nextRevision, valueRef=valueRef)
            self._values.update(replacements)


class CommittedValueTransaction(ValueLayer):
    """Nested speculative transaction with root-authoritative conflict detection.

    This is the Value System's transaction implementation. It is also a
    ValueLayer so ValueHandle works identically against committed and staged
    views. The logical address API therefore does not split into a separate
    transaction model for authoritative state.

    Nested transactions form a strict stack. While a child transaction remains
    active, its parent is suspended for public reads, writes, and creation of
    another child. This prevents ambiguous sibling ordering and parent mutation
    from racing semantically with child promotion inside one transaction tree.
    """

    def __init__(self, *, root: CommittedValueLayer, parent: "CommittedValueTransaction | None") -> None:
        super().__init__(parent=root if parent is None else parent)
        self._root = root
        self._transactionParent = parent
        self._staged: dict[ValueAddress, object] = {}
        self._bases: dict[ValueAddress, int] = {}
        self._children: set[CommittedValueTransaction] = set()
        self._state = "active"
        if parent is not None:
            parent._children.add(self)

    def openTransaction(self) -> "CommittedValueTransaction":
        self._requireActive()
        self._requireNoChildren()
        return CommittedValueTransaction(root=self._root, parent=self)

    def load(self, address: str | ValueAddress) -> object:
        """Materializes a value through this transaction's staged view."""
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        return self._loadValue(key)

    def set(self, address: str | ValueAddress, value: object) -> None:
        """Stages replacement at address through the authoritative transaction."""
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        self._setValue(key, value)

    def _loadValue(self, address: ValueAddress) -> object:
        self._requireActive()
        self._requireNoChildren()
        self._captureBase(address)
        return self._snapshot(self._loadVisible(address))

    def _loadLocalValue(self, address: ValueAddress) -> object:
        self._requireActive()
        self._requireNoChildren()
        self._captureBase(address)
        if address not in self._staged:
            return MISSING
        return self._snapshot(self._staged[address])

    def _setValue(self, address: ValueAddress, value: object) -> None:
        self._requireActive()
        self._requireNoChildren()
        self._captureBase(address)
        if value is MISSING:
            raise TypeError("MISSING represents Value System absence and cannot be staged as a value.")
        detached = self._snapshot(value)
        encodeJsonValue(detached, store=InMemoryChunkStore())
        self._staged[address] = detached

    def commit(self) -> None:
        self._requireActive()
        self._requireNoChildren()
        if self._transactionParent is None:
            self._root._commit(self._staged, self._bases)
        else:
            self._transactionParent._acceptChild(self._staged, self._bases)
        self._finish("committed")

    def abort(self) -> None:
        self._requireActive()
        self._requireNoChildren()
        self._finish("aborted")

    def _loadVisible(self, address: ValueAddress) -> object:
        if address in self._staged:
            return self._staged[address]
        if self._transactionParent is not None:
            return self._transactionParent._loadVisible(address)
        return self._root.load(address)

    def _captureBase(self, address: ValueAddress) -> None:
        if address not in self._bases:
            self._bases[address] = self._root.revisionId(address)

    def _acceptChild(self, staged: dict[ValueAddress, object], bases: dict[ValueAddress, int]) -> None:
        self._requireActive()
        detached = {address: self._snapshot(value) for address, value in staged.items()}
        for address, base in bases.items():
            existing = self._bases.setdefault(address, base)
            if existing != base:
                raise StateConflictError(f"Nested transaction base revision disagreement at {address}.")
        self._staged.update(detached)

    def _finish(self, state: str) -> None:
        self._staged.clear()
        self._bases.clear()
        self._state = state
        if self._transactionParent is not None:
            self._transactionParent._children.discard(self)

    def _requireActive(self) -> None:
        if self._state != "active":
            raise RuntimeError(f"Transaction is already {self._state}.")

    def _requireNoChildren(self) -> None:
        if self._children:
            raise RuntimeError("Transaction has an unresolved active child transaction.")

    @staticmethod
    def _snapshot(value: object) -> object:
        if value is MISSING:
            return MISSING
        try:
            return deepcopy(value)
        except Exception as err:
            raise TypeError(f"Transaction value cannot be detached: {type(value).__qualname__}.") from err
