# file: backend/values/committed.py ; version: 7
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock

from backend.core.errors import ActantError
from backend.values.address import ValueAddress
from backend.values.layer import ValueLayer
from backend.values.payload import ChunkValueRef, InMemoryChunkStore, ValueRef, decodeJsonValue, encodeJsonValue
from backend.values.sentinels import MISSING

__all__ = [
    "CommittedValueLayer",
    "CommittedValueTransaction",
    "StateConflictError",
    "ValueState",
]


class StateConflictError(ActantError, RuntimeError):
    """Raised when authoritative state changed after a transaction first touched an address."""


class ValueState(StrEnum):
    """Current authority state of one logical ValueAddress.

    PRESENT means an authoritative value is materializable.
    ABSENT means the current authoritative state says no value is present.
    INVALIDATED means a previously established result must not be trusted or
    reused until a producer establishes a new PRESENT or ABSENT revision.

    Revision 0 with ABSENT state represents an address for which no committed
    state has yet been established. A committed ABSENT state has revision 1 or
    greater and therefore remains distinguishable through revisionId().
    """

    PRESENT = "present"
    ABSENT = "absent"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class _CommittedRevision:
    revisionId: int
    state: ValueState
    valueRef: ValueRef | None


@dataclass(frozen=True, slots=True)
class _StagedRevision:
    state: ValueState
    value: object = MISSING


class CommittedValueLayer(ValueLayer):
    """Authoritative revisioned Value System layer backed by immutable ValueRefs.

    This is the single authoritative mutable Value System provider. Generic
    ValueLayer implementations may contribute read-only resolution views, but
    transaction creation, revision advancement, authority-state transitions,
    and conflict detection belong here so authoritative mutation has one
    project-wide semantic contract.

    Stable ValueAddress identity is independent of revision. Replacing a value,
    establishing authoritative absence, or invalidating the current result all
    update the same logical address and advance its memory-managed revision.
    Packs and applications must not implement their own version counters for
    values represented by this layer.

    INVALIDATED is intentionally retained as committed state rather than
    deleting an address. A producer can therefore distinguish "no value has
    ever been established" from "the previous derivation is known not to be
    reusable". Retention and historical-version storage are separate concerns;
    this layer currently retains only the latest committed revision.

    This contract is design-significant and should be promoted into the Value
    System design specification when implementation-level design is reconciled
    with the design documents.
    """

    def __init__(self, *, chunkStore: InMemoryChunkStore | None = None) -> None:
        super().__init__(parent=None)
        self.chunkStore = chunkStore or InMemoryChunkStore()
        self._values: dict[ValueAddress, _CommittedRevision] = {}
        self._lock = RLock()

    def load(self, address: str | ValueAddress) -> object:
        """Materializes the current authoritative value or MISSING when non-present."""
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        return self._loadLocalValue(key)

    def state(self, address: str | ValueAddress) -> ValueState:
        """Returns the current committed authority state for address."""
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        with self._lock:
            revision = self._values.get(key)
            return ValueState.ABSENT if revision is None else revision.state

    def revisionId(self, address: str | ValueAddress) -> int:
        """Returns the current committed revision, or 0 before any state is established."""
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        with self._lock:
            revision = self._values.get(key)
            return 0 if revision is None else revision.revisionId

    def openTransaction(self) -> CommittedValueTransaction:
        """Creates a speculative transaction against this authoritative layer."""
        return CommittedValueTransaction(root=self, parent=None)

    def _loadLocalValue(self, address: ValueAddress) -> object:
        with self._lock:
            revision = self._values.get(address)
        if revision is None or revision.state is not ValueState.PRESENT:
            return MISSING
        valueRef = revision.valueRef
        if valueRef is None:
            raise RuntimeError(f"Present committed value at {address} has no ValueRef.")
        return decodeJsonValue(valueRef, store=self.chunkStore)

    def _commit(
        self,
        staged: dict[ValueAddress, _StagedRevision],
        bases: dict[ValueAddress, int],
    ) -> None:
        """Atomically publishes one staged authoritative batch.

        All present values are encoded before the lock is acquired. Under the
        lock, every first-touch base revision is checked before any replacement
        is published. A conflict therefore leaves the complete prior
        authoritative state unchanged.
        """
        temporaryStore = InMemoryChunkStore()
        encoded: dict[ValueAddress, ValueRef] = {}
        for address, stagedRevision in staged.items():
            if stagedRevision.state is ValueState.PRESENT:
                encoded[address] = encodeJsonValue(stagedRevision.value, store=temporaryStore)

        with self._lock:
            for address, baseRevisionId in bases.items():
                currentRevision = self._values.get(address)
                current = 0 if currentRevision is None else currentRevision.revisionId
                if current != baseRevisionId:
                    raise StateConflictError(
                        f"State conflict at {address}: transaction observed revision "
                        f"{baseRevisionId}, current revision is {current}.",
                    )

            for valueRef in encoded.values():
                if isinstance(valueRef, ChunkValueRef):
                    self.chunkStore.put(temporaryStore.require(valueRef.chunkId))

            replacements: dict[ValueAddress, _CommittedRevision] = {}
            for address, stagedRevision in staged.items():
                current = self._values.get(address)
                nextRevision = 1 if current is None else current.revisionId + 1
                replacements[address] = _CommittedRevision(
                    revisionId=nextRevision,
                    state=stagedRevision.state,
                    valueRef=encoded.get(address),
                )
            self._values.update(replacements)


class CommittedValueTransaction(ValueLayer):
    """Speculative Value System transaction with root-authoritative conflicts.

    This is the Value System's transaction implementation. It is also a
    ValueLayer so ValueHandle works identically against committed and staged
    views. The logical-address API therefore does not split into a second
    transaction model for authoritative state.

    A transaction may stage PRESENT replacement, authoritative ABSENT state, or
    INVALIDATED state. None of those transitions become authoritative until the
    outermost transaction commits. Each committed transition advances the
    revision of the same stable ValueAddress.

    Nested transactions form a strict stack. While a child remains active, its
    parent is suspended for public reads, writes, invalidation, absence changes,
    and creation of another child. Child commit promotes staged state only into
    the parent transaction; only outer commit reaches authoritative state.

    This contract is design-significant and should be promoted into the Value
    System transaction specification when design documents are updated.
    """

    def __init__(
        self,
        *,
        root: CommittedValueLayer,
        parent: CommittedValueTransaction | None,
    ) -> None:
        super().__init__(parent=root if parent is None else parent)
        self._root = root
        self._transactionParent = parent
        self._staged: dict[ValueAddress, _StagedRevision] = {}
        self._bases: dict[ValueAddress, int] = {}
        self._children: set[CommittedValueTransaction] = set()
        self._state = "active"
        if parent is not None:
            parent._children.add(self)

    def openTransaction(self) -> CommittedValueTransaction:
        self._requireActive()
        self._requireNoChildren()
        return CommittedValueTransaction(root=self._root, parent=self)

    def load(self, address: str | ValueAddress) -> object:
        """Materializes a present value through this transaction's staged view."""
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        return self._loadValue(key)

    def state(self, address: str | ValueAddress) -> ValueState:
        """Returns the authority state visible through this transaction."""
        self._requireActive()
        self._requireNoChildren()
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        self._captureBase(key)
        return self._loadVisibleRevision(key).state

    def set(self, address: str | ValueAddress, value: object) -> None:
        """Stages a PRESENT replacement at address."""
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        self._setValue(key, value)

    def setAbsent(self, address: str | ValueAddress) -> None:
        """Stages authoritative absence at address without deleting its identity."""
        self._requireActive()
        self._requireNoChildren()
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        self._captureBase(key)
        self._staged[key] = _StagedRevision(state=ValueState.ABSENT)

    def invalidate(self, address: str | ValueAddress) -> None:
        """Stages INVALIDATED state so the current result cannot be reused."""
        self._requireActive()
        self._requireNoChildren()
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        self._captureBase(key)
        self._staged[key] = _StagedRevision(state=ValueState.INVALIDATED)

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

    def _loadValue(self, address: ValueAddress) -> object:
        self._requireActive()
        self._requireNoChildren()
        self._captureBase(address)
        revision = self._loadVisibleRevision(address)
        if revision.state is not ValueState.PRESENT:
            return MISSING
        return self._snapshot(revision.value)

    def _loadLocalValue(self, address: ValueAddress) -> object:
        self._requireActive()
        self._requireNoChildren()
        self._captureBase(address)
        staged = self._staged.get(address)
        if staged is None or staged.state is not ValueState.PRESENT:
            return MISSING
        return self._snapshot(staged.value)

    def _setValue(self, address: ValueAddress, value: object) -> None:
        self._requireActive()
        self._requireNoChildren()
        self._captureBase(address)
        if value is MISSING:
            raise TypeError("MISSING represents Value System absence and cannot be staged as a value.")
        detached = self._snapshot(value)
        encodeJsonValue(detached, store=InMemoryChunkStore())
        self._staged[address] = _StagedRevision(state=ValueState.PRESENT, value=detached)

    def _loadVisibleRevision(self, address: ValueAddress) -> _StagedRevision:
        staged = self._staged.get(address)
        if staged is not None:
            return staged
        if self._transactionParent is not None:
            return self._transactionParent._loadVisibleRevision(address)
        state = self._root.state(address)
        if state is not ValueState.PRESENT:
            return _StagedRevision(state=state)
        return _StagedRevision(state=state, value=self._root.load(address))

    def _captureBase(self, address: ValueAddress) -> None:
        if address not in self._bases:
            self._bases[address] = self._root.revisionId(address)

    def _acceptChild(
        self,
        staged: dict[ValueAddress, _StagedRevision],
        bases: dict[ValueAddress, int],
    ) -> None:
        self._requireActive()
        detached = {
            address: self._snapshotStaged(stagedRevision)
            for address, stagedRevision in staged.items()
        }
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

    @classmethod
    def _snapshotStaged(cls, stagedRevision: _StagedRevision) -> _StagedRevision:
        if stagedRevision.state is ValueState.PRESENT:
            return _StagedRevision(
                state=ValueState.PRESENT,
                value=cls._snapshot(stagedRevision.value),
            )
        return _StagedRevision(state=stagedRevision.state)

    @staticmethod
    def _snapshot(value: object) -> object:
        if value is MISSING:
            return MISSING
        try:
            return deepcopy(value)
        except Exception as err:
            raise TypeError(f"Transaction value cannot be detached: {type(value).__qualname__}.") from err
