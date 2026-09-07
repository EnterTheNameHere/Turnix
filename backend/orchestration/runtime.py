# file: backend/orchestration/runtime.py ; version: 6
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.core.runtimeIds import newRuntimeId
from backend.values.committed import CommittedValueLayer, CommittedValueTransaction

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
    """One bounded execution-authority unit inside an ApplicationRun.

    OrchestrationUnit outcome and transaction resolution remain distinct.
    Mutation-bearing units own one outer transaction, but successful workflow
    code must explicitly accept staged mutation before the unit is marked
    Completed. Failed, Cancelled, and Superseded units discard any unresolved
    staging.
    """

    orchestrationUnitId: str
    applicationRunId: str | None = None
    started: bool = False
    outcome: OrchestrationUnitOutcome | None = None
    _transaction: CommittedValueTransaction | None = None
    _mutationResolved: bool = False

    def __post_init__(self) -> None:
        if type(self.orchestrationUnitId) is not str or not self.orchestrationUnitId:
            raise ValueError("OrchestrationUnit.orchestrationUnitId must be a non-empty exact string.")
        if self.applicationRunId is not None and (
            type(self.applicationRunId) is not str or not self.applicationRunId
        ):
            raise ValueError("OrchestrationUnit.applicationRunId must be a non-empty string or None.")
        if type(self.started) is not bool:
            raise TypeError("OrchestrationUnit.started must be a bool.")
        if self.outcome is not None and not isinstance(self.outcome, OrchestrationUnitOutcome):
            raise TypeError("OrchestrationUnit.outcome must be an OrchestrationUnitOutcome or None.")
        if self._transaction is not None and not isinstance(self._transaction, CommittedValueTransaction):
            raise TypeError("OrchestrationUnit transaction must be a CommittedValueTransaction or None.")
        if self._transaction is not None and self.applicationRunId is None:
            raise ValueError("A mutation-bearing OrchestrationUnit requires applicationRunId.")

    @classmethod
    def new(cls) -> "OrchestrationUnit":
        return cls(orchestrationUnitId=newRuntimeId())

    @classmethod
    def mutation(
        cls,
        *,
        applicationRunId: str,
        transactionBase: CommittedValueLayer | CommittedValueTransaction,
    ) -> "OrchestrationUnit":
        if type(applicationRunId) is not str or not applicationRunId:
            raise ValueError("applicationRunId must be a non-empty string.")
        if not isinstance(transactionBase, (CommittedValueLayer, CommittedValueTransaction)):
            raise TypeError(
                "transactionBase must be a CommittedValueLayer or CommittedValueTransaction.",
            )
        return cls(
            orchestrationUnitId=newRuntimeId(),
            applicationRunId=applicationRunId,
            _transaction=transactionBase.openTransaction(),
        )

    def start(self) -> None:
        if self.started:
            raise RuntimeError("OrchestrationUnit is already started.")
        if self.outcome is not None:
            raise RuntimeError("Terminal OrchestrationUnit cannot start.")
        self.started = True

    @property
    def transactionId(self) -> str | None:
        return None if self._transaction is None else self._transaction.transactionId

    @property
    def memoryView(self) -> CommittedValueTransaction | None:
        return self._transaction

    @property
    def mutationResolved(self) -> bool:
        return self._transaction is None or self._mutationResolved

    def commitMutation(self) -> None:
        self._requireRunning()
        transaction = self._requireMutationTransaction()
        if self._mutationResolved:
            raise RuntimeError("OrchestrationUnit mutation is already resolved.")
        transaction.commit()
        self._mutationResolved = True

    def abortMutation(self) -> None:
        self._requireRunning()
        transaction = self._requireMutationTransaction()
        if self._mutationResolved:
            raise RuntimeError("OrchestrationUnit mutation is already resolved.")
        transaction.abort()
        self._mutationResolved = True

    def finish(self, outcome: OrchestrationUnitOutcome) -> None:
        self._requireRunning()
        if not isinstance(outcome, OrchestrationUnitOutcome):
            raise TypeError("OrchestrationUnit outcome must be an OrchestrationUnitOutcome.")

        if outcome is OrchestrationUnitOutcome.COMPLETED:
            if self._transaction is not None and not self._mutationResolved:
                raise RuntimeError(
                    "Completed mutation-bearing OrchestrationUnit requires explicit mutation resolution.",
                )
        elif self._transaction is not None and not self._mutationResolved:
            self._transaction.abort()
            self._mutationResolved = True

        self.outcome = outcome

    def _requireMutationTransaction(self) -> CommittedValueTransaction:
        if self._transaction is None:
            raise RuntimeError("OrchestrationUnit is not mutation-bearing.")
        return self._transaction

    def _requireNonTerminal(self) -> None:
        if self.outcome is not None:
            raise RuntimeError("OrchestrationUnit is already terminal.")

    def _requireRunning(self) -> None:
        self._requireNonTerminal()
        if not self.started:
            raise RuntimeError("OrchestrationUnit has not started.")
