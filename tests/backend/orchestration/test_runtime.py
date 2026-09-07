# file: tests/backend/orchestration/test_runtime.py ; version: 4
import pytest

from backend.orchestration.runtime import Job, JobState, OrchestrationUnit, OrchestrationUnitOutcome
from backend.values import CommittedValueLayer, MISSING


def test_job_failure_requires_exception_evidence():
    job = Job.new()
    job.start()

    with pytest.raises(TypeError, match="BaseException"):
        job.fail("not an exception")  # type: ignore[arg-type]

    assert job.state is JobState.RUNNING
    error = RuntimeError("failed")
    job.fail(error)
    assert job.state is JobState.FAILED
    assert job.error is error


def test_job_constructor_rejects_invalid_runtime_state():
    with pytest.raises(TypeError, match="Job.state"):
        Job(jobId="job", state="Running")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="jobId"):
        Job(jobId="")


def test_orchestration_unit_requires_declared_outcome():
    unit = OrchestrationUnit.new()
    unit.start()

    with pytest.raises(TypeError, match="OrchestrationUnit outcome"):
        unit.finish("Completed")  # type: ignore[arg-type]

    assert unit.outcome is None
    unit.finish(OrchestrationUnitOutcome.COMPLETED)
    assert unit.outcome is OrchestrationUnitOutcome.COMPLETED


def test_mutation_orchestration_unit_requires_explicit_successful_acceptance():
    root = CommittedValueLayer()
    unit = OrchestrationUnit.mutation(
        applicationRunId="application-run",
        transactionBase=root,
    )
    assert unit.memoryView is not None
    unit.start()

    child = unit.memoryView.openTransaction()
    child.set("test/value", {"accepted": True})
    child.commit()

    assert root.load("test/value") is MISSING
    assert unit.memoryView.load("test/value") == {"accepted": True}

    with pytest.raises(RuntimeError, match="explicit mutation resolution"):
        unit.finish(OrchestrationUnitOutcome.COMPLETED)

    unit.commitMutation()
    unit.finish(OrchestrationUnitOutcome.COMPLETED)

    assert unit.mutationResolved is True
    assert unit.outcome is OrchestrationUnitOutcome.COMPLETED
    assert root.load("test/value") == {"accepted": True}


@pytest.mark.parametrize(
    "outcome",
    [
        OrchestrationUnitOutcome.FAILED,
        OrchestrationUnitOutcome.CANCELLED,
        OrchestrationUnitOutcome.SUPERSEDED,
    ],
)
def test_non_completed_orchestration_unit_discards_unresolved_mutation(outcome):
    root = CommittedValueLayer()
    unit = OrchestrationUnit.mutation(
        applicationRunId="application-run",
        transactionBase=root,
    )
    assert unit.memoryView is not None
    unit.start()

    child = unit.memoryView.openTransaction()
    child.set("test/value", "must-not-propagate")
    child.commit()

    unit.finish(outcome)

    assert unit.outcome is outcome
    assert unit.mutationResolved is True
    assert root.load("test/value") is MISSING


def test_orchestration_unit_outcome_remains_distinct_from_transaction_resolution():
    root = CommittedValueLayer()
    unit = OrchestrationUnit.mutation(
        applicationRunId="application-run",
        transactionBase=root,
    )

    unit.start()
    unit.abortMutation()
    assert unit.outcome is None
    assert unit.mutationResolved is True

    unit.finish(OrchestrationUnitOutcome.COMPLETED)
    assert unit.outcome is OrchestrationUnitOutcome.COMPLETED


def test_mutation_orchestration_unit_can_nest_under_existing_parent_transaction():
    root = CommittedValueLayer()
    parent = root.openTransaction()
    unit = OrchestrationUnit.mutation(
        applicationRunId="application-run",
        transactionBase=parent,
    )
    unit.start()
    assert unit.memoryView is not None

    child = unit.memoryView.openTransaction()
    child.set("test/value", "nested")
    child.commit()

    unit.commitMutation()
    unit.finish(OrchestrationUnitOutcome.COMPLETED)

    assert root.load("test/value") is MISSING
    assert parent.load("test/value") == "nested"

    parent.commit()

    assert root.load("test/value") == "nested"


def test_failed_outcome_does_not_undo_already_committed_authoritative_mutation():
    root = CommittedValueLayer()
    unit = OrchestrationUnit.mutation(
        applicationRunId="application-run",
        transactionBase=root,
    )
    unit.start()
    assert unit.memoryView is not None

    unit.memoryView.set("test/value", "already-authoritative")
    unit.commitMutation()
    unit.finish(OrchestrationUnitOutcome.FAILED)

    assert unit.outcome is OrchestrationUnitOutcome.FAILED
    assert root.load("test/value") == "already-authoritative"
