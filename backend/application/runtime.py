# file: backend/application/runtime.py ; version: 3
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
    been established. Publication policy and generation tracking remain outside
    this domain object.
    """

    appPackId: str
    applicationId: str
    committedState: CommittedValueLayer = field(default_factory=CommittedValueLayer)
    saveBundleId: str | None = None

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

    Durable authoritative memory belongs to Application. The compatibility
    properties below intentionally expose the former ApplicationRun access
    path while runtime and pipeline code migrate toward Application ownership.
    Transactions opened during this run remain speculative until propagated to
    the Application's committed root.
    """

    application: Application
    applicationRunId: str = field(default_factory=newRuntimeId)
    state: ApplicationRunState = ApplicationRunState.CREATED

    @property
    def committedState(self) -> CommittedValueLayer:
        """Compatibility view of the Application-owned authoritative root."""
        return self.application.committedState

    @property
    def saveBundleId(self) -> str | None:
        """Compatibility view of the Application's bound SaveBundle lineage."""
        return self.application.saveBundleId

    @saveBundleId.setter
    def saveBundleId(self, value: str | None) -> None:
        if value is not None and (type(value) is not str or not value):
            raise ValueError("saveBundleId must be a non-empty string or None.")
        self.application.saveBundleId = value

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
