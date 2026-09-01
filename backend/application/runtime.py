# file: backend/application/runtime.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.runtimeIds import newRuntimeId

__all__ = ["Application", "ApplicationRun"]


@dataclass(frozen=True, slots=True)
class Application:
    """Durable identity of one Actant application individual."""

    applicationId: str

    @classmethod
    def new(cls) -> "Application":
        return cls(applicationId=newRuntimeId())


@dataclass(slots=True)
class ApplicationRun:
    """One live execution period of an Application."""

    application: Application
    applicationRunId: str = field(default_factory=newRuntimeId)
    active: bool = False

    def start(self) -> None:
        if self.active:
            raise RuntimeError("ApplicationRun is already active.")
        self.active = True

    def stop(self) -> None:
        self.active = False
