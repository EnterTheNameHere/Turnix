# file: backend/orchestration/runtime.py ; version: 8
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock

from backend.core.runtimeIds import newRuntimeId
from backend.orchestration.cancellation import CancellationSignal
from backend.values.committed import CommittedValueLayer, CommittedValueTransaction

__all__ = ["Job", "JobState", "OrchestrationUnit", "OrchestrationUnitOutcome"]


class JobState(StrEnum):
    """Primary observable Job lifecycle states."""

    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    TIMED_OUT = "TimedOut"
    SUPERSEDED = "Superseded"


class OrchestrationUnitOutcome(StrEnum):
    """Terminal outcomes for one OrchestrationUnit."""

    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    SUPERSEDED = "Superseded"


_JOB_TERMINAL_STATES = {
    JobState.SUCCEEDED,
    JobState.FAILED,
    JobState.CANCELLED,
    JobState.TIMED_OUT,
    JobState.SUPERSEDED,
}


@dataclass(slots=True)
class Job:
    """Observable requested-work lifecycle independent of execution mechanism."""

    jobId: str
    state: JobState = JobState.PENDING
    result: object | None = None
    error: BaseException | None = None
    authoritativeStateAccepted: bool = False
    _cancellationSignal: CancellationSignal = field(
        default_factory=CancellationSignal,
        init=False,
        repr=False,
        compare=False,
    )
    _lane: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validates one Job's externally supplied lifecycle fields."""
        if type(self.jobId) is not str or not self.jobId:
            raise ValueError("Job.jobId must be a non-empty exact string.")
        if not isinstance(self.state, JobState):
            raise TypeError("Job.state must be a JobState.")
        if self.error is not None and not isinstance(self.error, BaseException):
            raise TypeError("Job.error must be a BaseException or None.")
        if type(self.authoritativeStateAccepted) is not bool:
            raise TypeError("Job.authoritativeStateAccepted must be a bool.")

    @classmethod
    def new(cls) -> Job:
        """Creates one Pending Job with a new runtime identity."""
        return cls(jobId=newRuntimeId())

    @property
    def terminal(self) -> bool:
        """Reports whether this Job has reached its final lifecycle outcome."""
        with self._lane:
            return self.state in _JOB_TERMINAL_STATES

    @property
    def cancellationRequested(self) -> bool:
        """Reports whether cancellation has been requested for this Job."""
        return self._cancellationSignal.requested

    @property
    def cancellationSignal(self) -> CancellationSignal:
        """Returns the execution-facing signal owned by this Job."""
        return self._cancellationSignal

    def requestCancellation(self) -> bool:
        """
        Requests cancellation without conflating request with running-job outcome.

        A Pending Job can be cancelled immediately because no execution mechanism
        has started. A Running Job remains Running until its executor acknowledges
        the request and records the Cancelled terminal outcome.
        """
        with self._lane:
            if self.state in _JOB_TERMINAL_STATES:
                return False
            requested = self._cancellationSignal.request()
            if self.state is JobState.PENDING:
                self.state = JobState.CANCELLED
            return requested

    def start(self) -> None:
        """Starts this Job unless it was cancelled while still Pending."""
        with self._lane:
            if self.state is not JobState.PENDING:
                raise RuntimeError("Only a Pending Job can start.")
            if self._cancellationSignal.requested:
                self.state = JobState.CANCELLED
                return
            self.state = JobState.RUNNING

    def succeed(self, result: object = None) -> None:
        """Records successful terminal completion."""
        self._finish(JobState.SUCCEEDED, result=result)

    def fail(self, error: BaseException) -> None:
        """Records failure terminal completion with exception evidence."""
        if not isinstance(error, BaseException):
            raise TypeError("Job failure must carry a BaseException.")
        self._finish(JobState.FAILED, error=error)

    def cancel(self) -> None:
        """Records intentional cancellation after running execution acknowledges it."""
        self._finish(JobState.CANCELLED)

    def _finish(
        self,
        state: JobState,
        *,
        result: object | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Transitions a Running Job to exactly one terminal outcome."""
        with self._lane:
            if self.state is not JobState.RUNNING:
                raise RuntimeError("Only a Running Job can become terminal.")
            if not isinstance(state, JobState):
                raise TypeError("Terminal Job state must be a JobState.")
            if state not in _JOB_TERMINAL_STATES:
                raise ValueError("Job terminal transition requires a terminal JobState.")
            if error is not None and not isinstance(error, BaseException):
                raise TypeError("Terminal Job error must be a BaseException or None.")
            self.state = state
            self.result = result
            self.error = error


@dataclass(slots=True)
class OrchestrationUnit:
    """
    One bounded execution-authority unit inside an ApplicationRun.

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
        """Validates one OrchestrationUnit's identity and transaction shape."""
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
    def new(cls) -> OrchestrationUnit:
        """Creates one non-mutation-bearing OrchestrationUnit."""
        return cls(orchestrationUnitId=newRuntimeId())

    @classmethod
    def mutation(
        cls,
        *,
        applicationRunId: str,
        transactionBase: CommittedValueLayer | CommittedValueTransaction,
    ) -> OrchestrationUnit:
        """Creates one mutation-bearing unit with a new outer transaction."""
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
        """Starts this unit exactly once."""
        if self.started:
            raise RuntimeError("OrchestrationUnit is already started.")
        if self.outcome is not None:
            raise RuntimeError("Terminal OrchestrationUnit cannot start.")
        self.started = True

    @property
    def transactionId(self) -> str | None:
        """Returns the owned transaction identity when mutation-bearing."""
        return None if self._transaction is None else self._transaction.transactionId

    @property
    def memoryView(self) -> CommittedValueTransaction | None:
        """Returns the owned speculative transaction when mutation-bearing."""
        return self._transaction

    @property
    def mutationResolved(self) -> bool:
        """Reports whether this unit has no unresolved mutation staging."""
        return self._transaction is None or self._mutationResolved

    def commitMutation(self) -> None:
        """Accepts this unit's staged mutation into its transaction parent."""
        self._requireRunning()
        transaction = self._requireMutationTransaction()
        if self._mutationResolved:
            raise RuntimeError("OrchestrationUnit mutation is already resolved.")
        transaction.commit()
        self._mutationResolved = True

    def abortMutation(self) -> None:
        """Discards this unit's unresolved staged mutation."""
        self._requireRunning()
        transaction = self._requireMutationTransaction()
        if self._mutationResolved:
            raise RuntimeError("OrchestrationUnit mutation is already resolved.")
        transaction.abort()
        self._mutationResolved = True

    def finish(self, outcome: OrchestrationUnitOutcome) -> None:
        """Records one declared terminal outcome and resolves staging as required."""
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
        """Returns this unit's transaction or rejects non-mutation-bearing use."""
        if self._transaction is None:
            raise RuntimeError("OrchestrationUnit is not mutation-bearing.")
        return self._transaction

    def _requireNonTerminal(self) -> None:
        """Requires this unit not to have a terminal outcome."""
        if self.outcome is not None:
            raise RuntimeError("OrchestrationUnit is already terminal.")

    def _requireRunning(self) -> None:
        """Requires this unit to be started and non-terminal."""
        self._requireNonTerminal()
        if not self.started:
            raise RuntimeError("OrchestrationUnit has not started.")
