# file: tests/backend/orchestration/test_jobCancellation.py ; version: 1
from backend.orchestration import ExecutionCancelled, Job, JobState


def test_pending_job_cancellation_is_immediately_terminal() -> None:
    """Pending work can become Cancelled without ever entering Running."""
    job = Job.new()

    assert job.requestCancellation() is True
    assert job.cancellationRequested is True
    assert job.state is JobState.CANCELLED
    assert job.terminal is True
    assert job.requestCancellation() is False


def test_running_job_cancellation_request_is_distinct_from_terminal_outcome() -> None:
    """Running work stays Running until execution acknowledges cancellation."""
    job = Job.new()
    job.start()

    assert job.requestCancellation() is True
    assert job.cancellationRequested is True
    assert job.state is JobState.RUNNING

    try:
        job.cancellationSignal.raiseIfRequested()
    except ExecutionCancelled:
        job.cancel()

    assert job.state is JobState.CANCELLED
    assert job.result is None
    assert job.error is None
