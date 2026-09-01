# file: backend/orchestration/runtime.py ; version: 1
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.core.ids import uuidv4hex

__all__ = ["Job", "JobState", "OrchestrationUnit", "OrchestrationUnitOutcome"]


class JobState(StrEnum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    TIMED_OUT = "TimedOut"
    SUPERSEDED = "Superseded"


class OrchestrationUnitOutcome(StrEnum):
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    SUPERSEDED = "Superseded"


@dataclass(slots=True)
class Job:
    """Observable requested-work lifecycle independent of execution mechanism."""

    jobId: str
    state: JobState = JobState.PENDING
    result: object | None = None
    error: BaseException | None = None

    @classmethod
    def new(cls) -> "Job":
        return cls(jobId=uuidv4hex())

    def start(self) -> None:
        if self.state is not JobState.PENDING:
            raise RuntimeError("Only a Pending Job can start.")
        self.state = JobState.RUNNING

    def succeed(self, result: object = None) -> None:
        self._finish(JobState.SUCCEEDED, result=result)

    def fail(self, error: BaseException) -> None:
        self._finish(JobState.FAILED, error=error)

    def cancel(self) -> None:
        self._finish(JobState.CANCELLED)

    def _finish(self, state: JobState, *, result: object | None = None, error: BaseException | None = None) -> None:
        if self.state is not JobState.RUNNING:
            raise RuntimeError("Only a Running Job can become terminal.")
        self.state = state
        self.result = result
        self.error = error


@dataclass(slots=True)
class OrchestrationUnit:
    """One bounded execution-authority unit inside an ApplicationRun."""

    orchestrationUnitId: str
    outcome: OrchestrationUnitOutcome | None = None

    @classmethod
    def new(cls) -> "OrchestrationUnit":
        return cls(orchestrationUnitId=uuidv4hex())

    def finish(self, outcome: OrchestrationUnitOutcome) -> None:
        if self.outcome is not None:
            raise RuntimeError("OrchestrationUnit is already terminal.")
        self.outcome = outcome
