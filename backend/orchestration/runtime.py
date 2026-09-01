# file: backend/orchestration/runtime.py ; version: 3
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.core.runtimeIds import newRuntimeId

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

    def __post_init__(self) -> None:
        if type(self.jobId) is not str or not self.jobId:
            raise ValueError("Job.jobId must be a non-empty exact string.")
        if not isinstance(self.state, JobState):
            raise TypeError("Job.state must be a JobState.")
        if self.error is not None and not isinstance(self.error, BaseException):
            raise TypeError("Job.error must be a BaseException or None.")

    @classmethod
    def new(cls) -> "Job":
        return cls(jobId=newRuntimeId())

    def start(self) -> None:
        if self.state is not JobState.PENDING:
            raise RuntimeError("Only a Pending Job can start.")
        self.state = JobState.RUNNING

    def succeed(self, result: object = None) -> None:
        self._finish(JobState.SUCCEEDED, result=result)

    def fail(self, error: BaseException) -> None:
        if not isinstance(error, BaseException):
            raise TypeError("Job failure must carry a BaseException.")
        self._finish(JobState.FAILED, error=error)

    def cancel(self) -> None:
        self._finish(JobState.CANCELLED)

    def _finish(self, state: JobState, *, result: object | None = None, error: BaseException | None = None) -> None:
        if self.state is not JobState.RUNNING:
            raise RuntimeError("Only a Running Job can become terminal.")
        if not isinstance(state, JobState):
            raise TypeError("Terminal Job state must be a JobState.")
        if error is not None and not isinstance(error, BaseException):
            raise TypeError("Terminal Job error must be a BaseException or None.")
        self.state = state
        self.result = result
        self.error = error


@dataclass(slots=True)
class OrchestrationUnit:
    """One bounded execution-authority unit inside an ApplicationRun."""

    orchestrationUnitId: str
    outcome: OrchestrationUnitOutcome | None = None

    def __post_init__(self) -> None:
        if type(self.orchestrationUnitId) is not str or not self.orchestrationUnitId:
            raise ValueError("OrchestrationUnit.orchestrationUnitId must be a non-empty exact string.")
        if self.outcome is not None and not isinstance(self.outcome, OrchestrationUnitOutcome):
            raise TypeError("OrchestrationUnit.outcome must be an OrchestrationUnitOutcome or None.")

    @classmethod
    def new(cls) -> "OrchestrationUnit":
        return cls(orchestrationUnitId=newRuntimeId())

    def finish(self, outcome: OrchestrationUnitOutcome) -> None:
        if self.outcome is not None:
            raise RuntimeError("OrchestrationUnit is already terminal.")
        if not isinstance(outcome, OrchestrationUnitOutcome):
            raise TypeError("OrchestrationUnit outcome must be an OrchestrationUnitOutcome.")
        self.outcome = outcome
