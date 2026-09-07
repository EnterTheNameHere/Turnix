# file: backend/save/runtime.py ; version: 4
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass

from backend.core.runtimeIds import newRuntimeId
from backend.values.committed import CommittedValueLayer

__all__ = ["SaveBundle"]


@dataclass(frozen=True, slots=True)
class SaveBundle:
    """Immutable persistence-closure snapshot for one Application generation.

    SaveBundle contains a persistence representation of authoritative committed
    state. It does not make state authoritative: values are included only after
    they have crossed Actant's commit boundary.

    SaveBundle identity is distinct from Application identity. A new generation
    is a new immutable snapshot object with the same saveBundleId/applicationId
    and an incremented generation number.

    This implementation owns representation, validation, and rehydration only.
    It does not write filesystem paths or claim persistent-I/O publication;
    storage authority remains a separate boundary.

    This implementation-level contract is design-significant and should be
    promoted into the SaveBundle implementation specification later.
    """

    saveBundleId: str
    appPackId: str
    applicationId: str
    generation: int
    _committedStateSnapshot: dict[str, object]

    _FORMAT_ID = "actant.save-bundle@1"

    def __post_init__(self) -> None:
        if type(self.saveBundleId) is not str or not self.saveBundleId:
            raise ValueError("saveBundleId must be a non-empty string.")
        if type(self.appPackId) is not str or not self.appPackId:
            raise ValueError("appPackId must be a non-empty string.")
        if type(self.applicationId) is not str or not self.applicationId:
            raise ValueError("applicationId must be a non-empty string.")
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("generation must be a positive exact integer.")
        if not isinstance(self._committedStateSnapshot, dict):
            raise TypeError("committedStateSnapshot must be an object.")

        CommittedValueLayer.fromSnapshot(self._committedStateSnapshot)
        object.__setattr__(
            self,
            "_committedStateSnapshot",
            deepcopy(self._committedStateSnapshot),
        )

    @classmethod
    def create(
        cls,
        *,
        appPackId: str,
        applicationId: str,
        committedState: CommittedValueLayer,
    ) -> SaveBundle:
        """Creates generation 1 for one durable Application."""
        if not isinstance(committedState, CommittedValueLayer):
            raise TypeError("committedState must be a CommittedValueLayer.")
        return cls(
            saveBundleId=newRuntimeId(),
            appPackId=appPackId,
            applicationId=applicationId,
            generation=1,
            _committedStateSnapshot=committedState.snapshot(),
        )

    def nextGeneration(self, *, committedState: CommittedValueLayer) -> SaveBundle:
        """Captures the immediately following generation of this SaveBundle identity."""
        return self.advanceToGeneration(
            generation=self.generation + 1,
            committedState=committedState,
        )

    def advanceToGeneration(
        self,
        *,
        generation: int,
        committedState: CommittedValueLayer,
    ) -> SaveBundle:
        """Captures a later generation while preserving immutable recovery gaps.

        Normal publication uses nextGeneration(). Recovery may need to advance
        beyond an occupied corrupt generation without overwriting that evidence.
        """
        if type(generation) is not int or generation <= self.generation:
            raise ValueError("generation must be an exact integer greater than the current bundle generation.")
        if not isinstance(committedState, CommittedValueLayer):
            raise TypeError("committedState must be a CommittedValueLayer.")
        return SaveBundle(
            saveBundleId=self.saveBundleId,
            appPackId=self.appPackId,
            applicationId=self.applicationId,
            generation=generation,
            _committedStateSnapshot=committedState.snapshot(),
        )

    def restoreCommittedState(self) -> CommittedValueLayer:
        """Creates a fresh live committed layer from retained authoritative state."""
        return CommittedValueLayer.fromSnapshot(deepcopy(self._committedStateSnapshot))

    def snapshot(self) -> dict[str, object]:
        """Returns a detached JSON-compatible SaveBundle snapshot."""
        return {
            "formatId": self._FORMAT_ID,
            "saveBundleId": self.saveBundleId,
            "appPackId": self.appPackId,
            "applicationId": self.applicationId,
            "generation": self.generation,
            "committedState": deepcopy(self._committedStateSnapshot),
        }

    def toBytes(self) -> bytes:
        """Returns deterministic canonical UTF-8 JSON bytes for persistence."""
        return json.dumps(
            self.snapshot(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def fromSnapshot(cls, snapshot: object) -> SaveBundle:
        """Validates and rehydrates a SaveBundle snapshot."""
        if not isinstance(snapshot, dict):
            raise TypeError("SaveBundle snapshot must be an object.")
        if snapshot.get("formatId") != cls._FORMAT_ID:
            raise ValueError(f"SaveBundle requires formatId {cls._FORMAT_ID!r}.")
        committedStateSnapshot = snapshot.get("committedState")
        if not isinstance(committedStateSnapshot, dict):
            raise TypeError("SaveBundle committedState must be an object.")
        return cls(
            saveBundleId=snapshot.get("saveBundleId"),
            appPackId=snapshot.get("appPackId"),
            applicationId=snapshot.get("applicationId"),
            generation=snapshot.get("generation"),
            _committedStateSnapshot=committedStateSnapshot,
        )

    @classmethod
    def fromBytes(cls, payload: bytes) -> SaveBundle:
        """Decodes canonical-compatible JSON bytes and validates the bundle."""
        if type(payload) is not bytes:
            raise TypeError("SaveBundle payload must be exact bytes.")
        try:
            snapshot = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ValueError("SaveBundle payload is not valid UTF-8 JSON.") from err
        return cls.fromSnapshot(snapshot)
