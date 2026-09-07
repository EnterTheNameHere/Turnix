# file: backend/application/runtime.py ; version: 6
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from backend.core.runtimeIds import newRuntimeId
from backend.values.committed import CommittedValueLayer

__all__ = ["Application", "ApplicationRun", "ApplicationRunState"]


@dataclass(slots=True)
class Application:
    """One durable Actant Application identity and its authoritative root.

    committedState belongs to the Application because it is the state that
    survives across ApplicationRuns through SaveBundle persistence. A run only
    provides one non-restartable runtime incarnation over this same conceptual
    Application state.

    saveBundleId identifies the currently bound SaveBundle lineage when one has
    been established. durableGeneration is the highest filesystem generation
    slot that must never be reused: either accepted by the current pointer or
    already occupied by an immutable generation artifact. It is persistence
    sequencing metadata, not a committed-root revision or checkpoint.
    """

    appPackId: str
    applicationId: str
    committedState: CommittedValueLayer = field(default_factory=CommittedValueLayer)
    saveBundleId: str | None = None
    durableGeneration: int | None = None

    def __post_init__(self) -> None:
        if type(self.appPackId) is not str or not self.appPackId:
            raise ValueError("appPackId must be a non-empty string.")
        if type(self.applicationId) is not str or not self.applicationId:
            raise ValueError("applicationId must be a non-empty string.")
        if not isinstance(self.committedState, CommittedValueLayer):
            raise TypeError("committedState must be a CommittedValueLayer.")
        if self.saveBundleId is not None and (
            type(self.saveBundleId) is not str or not self.saveBundleId
        ):
            raise ValueError("saveBundleId must be a non-empty string or None.")
        if self.durableGeneration is not None and (
            type(self.durableGeneration) is not int or self.durableGeneration <= 0
        ):
            raise ValueError("durableGeneration must be a positive exact integer or None.")

    @classmethod
    def new(cls, *, appPackId: str) -> "Application":
        return cls(appPackId=appPackId, applicationId=newRuntimeId())


class ApplicationRunState(StrEnum):
    CREATED = "Created"
    ACTIVE = "Active"
    STOPPED = "Stopped"


@dataclass(slots=True)
class ApplicationRun:
    """One non-restartable live execution period of an Application.

    Durable authoritative memory and SaveBundle lineage belong to Application.
    ApplicationRun owns only the identity and lifecycle state of one live
    execution incarnation.
    """

    application: Application
    applicationRunId: str = field(default_factory=newRuntimeId)
    state: ApplicationRunState = ApplicationRunState.CREATED

    @property
    def active(self) -> bool:
        return self.state is ApplicationRunState.ACTIVE

    def start(self) -> None:
        if self.state is ApplicationRunState.ACTIVE:
            raise RuntimeError("ApplicationRun is already active.")
        if self.state is ApplicationRunState.STOPPED:
            raise RuntimeError("A stopped ApplicationRun cannot be restarted; create a new ApplicationRun.")
        self.state = ApplicationRunState.ACTIVE

    def stop(self) -> None:
        if self.state is ApplicationRunState.CREATED:
            raise RuntimeError("ApplicationRun cannot stop before it has started.")
        if self.state is ApplicationRunState.STOPPED:
            raise RuntimeError("ApplicationRun is already stopped.")
        self.state = ApplicationRunState.STOPPED
