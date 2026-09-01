# file: backend/values/committed.py ; version: 3
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock

from backend.values.address import ValueAddress
from backend.values.payload import ChunkValueRef, InMemoryChunkStore, ValueRef, decodeJsonValue, encodeJsonValue
from backend.values.sentinels import MISSING

__all__ = ["CommittedValueLayer", "CommittedValueTransaction", "StateConflictError"]


class StateConflictError(RuntimeError):
    """Raised when authoritative state changed after a transaction first touched a key."""


@dataclass(frozen=True, slots=True)
class _CommittedRevision:
    revisionId: int
    valueRef: ValueRef


class CommittedValueLayer:
    """Authoritative revisioned value layer backed by immutable ValueRefs.

    Commit conflict validation and revision replacement occur under one lock so
    two concurrent outer transactions cannot both validate against the same
    base revision and silently overwrite each other. Payload encoding is staged
    in a temporary Chunk store; failed conflict validation therefore does not
    leave newly encoded unreachable Chunks in the authoritative store.
    """

    def __init__(self, *, chunkStore: InMemoryChunkStore | None = None) -> None:
        self.chunkStore = chunkStore or InMemoryChunkStore()
        self._values: dict[ValueAddress, _CommittedRevision] = {}
        self._lock = RLock()

    def load(self, address: str | ValueAddress) -> object:
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        with self._lock:
            revision = self._values.get(key)
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

            # Promote immutable payload material only after conflict validation.
            # Chunk-store insertion precedes authoritative references, so a
            # storage failure cannot create a committed reference to missing data.
            for valueRef in encoded.values():
                if isinstance(valueRef, ChunkValueRef):
                    self.chunkStore.put(temporaryStore.require(valueRef.chunkId))

            replacements: dict[ValueAddress, _CommittedRevision] = {}
            for address, valueRef in encoded.items():
                current = self._values.get(address)
                nextRevision = 1 if current is None else current.revisionId + 1
                replacements[address] = _CommittedRevision(revisionId=nextRevision, valueRef=valueRef)
            self._values.update(replacements)


class CommittedValueTransaction:
    """Nested speculative transaction with root-authoritative conflict detection.

    Values are detached when staged, loaded, and promoted between nested
    transactions. Mutable caller aliases therefore cannot modify transaction
    state without an explicit set().
    """

    def __init__(self, *, root: CommittedValueLayer, parent: "CommittedValueTransaction | None") -> None:
        self._root = root
        self._parent = parent
        self._staged: dict[ValueAddress, object] = {}
        self._bases: dict[ValueAddress, int] = {}
        self._children: set[CommittedValueTransaction] = set()
        self._state = "active"
        if parent is not None:
            parent._children.add(self)

    def openTransaction(self) -> "CommittedValueTransaction":
        self._requireActive()
        return CommittedValueTransaction(root=self._root, parent=self)

    def load(self, address: str | ValueAddress) -> object:
        self._requireActive()
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        self._captureBase(key)
        return self._snapshot(self._loadVisible(key))

    def set(self, address: str | ValueAddress, value: object) -> None:
        self._requireActive()
        key = address if isinstance(address, ValueAddress) else ValueAddress(address)
        self._captureBase(key)
        detached = self._snapshot(value)
        # Encoding now validates deterministic authoritative representation;
        # the created temporary ref is intentionally discarded until commit.
        encodeJsonValue(detached, store=InMemoryChunkStore())
        self._staged[key] = detached

    def commit(self) -> None:
        self._requireActive()
        self._requireNoChildren()
        if self._parent is None:
            self._root._commit(self._staged, self._bases)
        else:
            self._parent._acceptChild(self._staged, self._bases)
        self._finish("committed")

    def abort(self) -> None:
        self._requireActive()
        self._requireNoChildren()
        self._finish("aborted")

    def _loadVisible(self, address: ValueAddress) -> object:
        if address in self._staged:
            return self._staged[address]
        if self._parent is not None:
            return self._parent._loadVisible(address)
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
        if self._parent is not None:
            self._parent._children.discard(self)

    def _requireActive(self) -> None:
        if self._state != "active":
            raise RuntimeError(f"Transaction is already {self._state}.")

    def _requireNoChildren(self) -> None:
        if self._children:
            raise RuntimeError("Transaction has unresolved active child transactions.")

    @staticmethod
    def _snapshot(value: object) -> object:
        if value is MISSING:
            return MISSING
        try:
            return deepcopy(value)
        except Exception as err:
            raise TypeError(f"Transaction value cannot be detached: {type(value).__qualname__}.") from err
