# file: backend/application/runtime.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from backend.core.runtimeIds import newRuntimeId
from backend.values.committed import CommittedValueLayer

__all__ = ["Application", "ApplicationRun", "ApplicationRunState"]


@dataclass(frozen=True, slots=True)
class Application:
    """Durable identity of one Actant application individual."""

    appPackId: str
    applicationId: str

    def __post_init__(self) -> None:
        if type(self.appPackId) is not str or not self.appPackId:
            raise ValueError("appPackId must be a non-empty string.")
        if type(self.applicationId) is not str or not self.applicationId:
            raise ValueError("applicationId must be a non-empty string.")

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

    committedState is the live authoritative Value layer for this run. It may
    begin empty for a new Application or be rehydrated from the Application's
    bound SaveBundle when loading an existing durable Application.

    The layer is runtime residency, not persistence identity. saveBundleId
    records the active persistence closure when one is bound; the SaveBundle
    owns durable cross-run continuity. ProcessingRuns and Pack CodeEntries open
    speculative transactions against the same committedState object.

    This boundary follows DA-04/DA-20: ApplicationRun is live execution,
    Application is durable identity, and SaveBundle is durable persistence
    closure.
    """

    application: Application
    applicationRunId: str = field(default_factory=newRuntimeId)
    committedState: CommittedValueLayer = field(default_factory=CommittedValueLayer)
    saveBundleId: str | None = None
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
