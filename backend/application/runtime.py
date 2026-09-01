from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from backend.core.runtimeIds import newRuntimeId
from backend.values.committed import CommittedValueLayer

__all__ = ["Application", "ApplicationRun", "ApplicationRunState"]


@dataclass(frozen=True, slots=True)
class Application:
    """Durable identity of one Actant application individual."""

    applicationId: str

    @classmethod
    def new(cls) -> "Application":
        return cls(applicationId=newRuntimeId())


class ApplicationRunState(StrEnum):
    CREATED = "Created"
    ACTIVE = "Active"
    STOPPED = "Stopped"


@dataclass(slots=True)
class ApplicationRun:
    """One non-restartable live execution period of an Application.

    committedState is authoritative ApplicationRun-scoped state. ProcessingRuns
    open speculative transactions against it and only successful pipeline
    completion may cross the outer commit boundary.
    """

    application: Application
    applicationRunId: str = field(default_factory=newRuntimeId)
    committedState: CommittedValueLayer = field(default_factory=CommittedValueLayer)
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
