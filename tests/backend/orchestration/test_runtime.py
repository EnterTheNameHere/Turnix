import pytest

from backend.orchestration.runtime import Job, JobState, OrchestrationUnit, OrchestrationUnitOutcome


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

    with pytest.raises(TypeError, match="OrchestrationUnit outcome"):
        unit.finish("Completed")  # type: ignore[arg-type]

    assert unit.outcome is None
    unit.finish(OrchestrationUnitOutcome.COMPLETED)
    assert unit.outcome is OrchestrationUnitOutcome.COMPLETED
