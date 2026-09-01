# file: backend/application/runtime.py ; version: 3
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from backend.core.runtimeIds import newRuntimeId

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
    """One non-restartable live execution period of an Application."""

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
