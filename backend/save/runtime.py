# file: backend/save/runtime.py ; version: 1
from __future__ import annotations

import json
from copy import deepcopy

from backend.core.runtimeIds import newRuntimeId
from backend.values.committed import CommittedValueLayer

__all__ = ["SaveBundle"]


class SaveBundle:
    """Durable persistence closure snapshot for one Application.

    SaveBundle contains a persistence representation of authoritative committed
    state. It does not make state authoritative: values are included only after
    they have crossed Actant's commit boundary.

    A SaveBundle identity is distinct from Application identity and survives
    generation updates. Each published replacement generation keeps the same
    saveBundleId and applicationId while incrementing generation.

    This implementation intentionally owns representation, validation, and
    rehydration only. It does not write filesystem paths or claim persistent
    I/O authority; DA-07 owns the storage mechanism that eventually publishes
    these bytes durably.

    The current bundle contains committed Value state only. Other durable
    Application material (accepted graph evidence, configuration, Windows,
    retained evidence, Pack material, and retention metadata) can be added to
    later format revisions without making Pack code responsible for persistence.

    This implementation-level contract is design-significant and should be
    promoted into the SaveBundle implementation specification when one is
    established.
    """

    __slots__ = (
        "_committedStateSnapshot",
        "applicationId",
        "generation",
        "saveBundleId",
    )

    _FORMAT_ID = "actant.save-bundle@1"

    def __init__(
        self,
        *,
        saveBundleId: str,
        applicationId: str,
        generation: int,
        committedStateSnapshot: dict[str, object],
    ) -> None:
        if type(saveBundleId) is not str or not saveBundleId:
            raise ValueError("saveBundleId must be a non-empty string.")
        if type(applicationId) is not str or not applicationId:
            raise ValueError("applicationId must be a non-empty string.")
        if type(generation) is not int or generation <= 0:
            raise ValueError("generation must be a positive exact integer.")
        if not isinstance(committedStateSnapshot, dict):
            raise TypeError("committedStateSnapshot must be an object.")

        # Validate before retaining. Rehydration also proves referenced chunks,
        # codecs, ValueRefs, revisions, addresses, and authority states.
        CommittedValueLayer.fromSnapshot(committedStateSnapshot)

        self.saveBundleId = saveBundleId
        self.applicationId = applicationId
        self.generation = generation
        self._committedStateSnapshot = deepcopy(committedStateSnapshot)

    @classmethod
    def create(
        cls,
        *,
        applicationId: str,
        committedState: CommittedValueLayer,
    ) -> SaveBundle:
        """Creates generation 1 for one durable Application."""
        if not isinstance(committedState, CommittedValueLayer):
            raise TypeError("committedState must be a CommittedValueLayer.")
        return cls(
            saveBundleId=newRuntimeId(),
            applicationId=applicationId,
            generation=1,
            committedStateSnapshot=committedState.snapshot(),
        )

    def nextGeneration(self, *, committedState: CommittedValueLayer) -> SaveBundle:
        """Captures the next generation of this same SaveBundle identity."""
        if not isinstance(committedState, CommittedValueLayer):
            raise TypeError("committedState must be a CommittedValueLayer.")
        return SaveBundle(
            saveBundleId=self.saveBundleId,
            applicationId=self.applicationId,
            generation=self.generation + 1,
            committedStateSnapshot=committedState.snapshot(),
        )

    def restoreCommittedState(self) -> CommittedValueLayer:
        """Creates a fresh live committed layer from retained authoritative state."""
        return CommittedValueLayer.fromSnapshot(deepcopy(self._committedStateSnapshot))

    def snapshot(self) -> dict[str, object]:
        """Returns a detached JSON-compatible SaveBundle snapshot."""
        return {
            "formatId": self._FORMAT_ID,
            "saveBundleId": self.saveBundleId,
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
            applicationId=snapshot.get("applicationId"),
            generation=snapshot.get("generation"),
            committedStateSnapshot=committedStateSnapshot,
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
