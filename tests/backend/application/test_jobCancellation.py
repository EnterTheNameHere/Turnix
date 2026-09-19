# file: tests/backend/application/test_jobCancellation.py ; version: 2
from pathlib import Path
from threading import Event, Thread

from backend.application.applicationRuntime import ApplicationRuntime
from backend.context import CodeEntryContext, CodeEntryIdentity
from backend.orchestration import Job, JobState
from backend.packs.runtime import PackResolver
from backend.registration import RegistrationScope
from backend.values import MISSING


def test_running_job_can_be_cancelled_from_another_thread_without_committing() -> None:
    """A retained Job handle cancels running capability work and discards staging."""
    runtime = ApplicationRuntime(
        appPackId="test.app",
        packResolver=PackResolver(roots=()),
    )
    runtime.start()
    identity = CodeEntryIdentity(
        applicationId=runtime.applicationRun.application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="cancel-job-entry",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )
    runtime.registerCodeEntry(identity, Path.cwd())
    scope = RegistrationScope()
    entered = Event()
    continueExecution = Event()

    def handler(ctx: CodeEntryContext, _payload: object | None) -> object:
        """Stages mutation, waits for cancellation, then cooperatively checks it."""
        transaction = ctx.memory.openTransaction()
        transaction.set("test/job/cancelled", "must-not-commit")
        transaction.commit()
        entered.set()
        if not continueExecution.wait(timeout=5):
            raise TimeoutError("Test did not release the running capability.")
        ctx.cancellation.raiseIfRequested()
        return "must-not-succeed"

    runtime.capabilities.register(
        scope,
        ownerId=identity.codeEntryInstanceId,
        capabilityId="test.job.cancel@1",
        handler=handler,
    )
    scope.publish()

    job = Job.new()
    completed: list[Job] = []

    def run() -> None:
        """Runs the blocking Job while the test retains its cancellation handle."""
        completed.append(runtime.runJob("test.job.cancel@1", job=job))

    worker = Thread(target=run)
    try:
        worker.start()
        assert entered.wait(timeout=5)
        assert job.state is JobState.RUNNING

        assert job.requestCancellation() is True
        continueExecution.set()
        worker.join(timeout=5)

        assert worker.is_alive() is False
        assert completed == [job]
        assert job.state is JobState.CANCELLED
        assert job.authoritativeStateAccepted is False
        assert runtime.applicationRun.application.committedState.load("test/job/cancelled") is MISSING
    finally:
        continueExecution.set()
        worker.join(timeout=5)
        scope.withdraw()
        runtime.unregisterCodeEntry(identity.codeEntryInstanceId)
        runtime.close()


def test_pre_cancelled_job_does_not_invoke_capability() -> None:
    """A Job cancelled while Pending never creates capability execution."""
    runtime = ApplicationRuntime(
        appPackId="test.app",
        packResolver=PackResolver(roots=()),
    )
    runtime.start()
    job = Job.new()
    assert job.requestCancellation() is True

    try:
        returned = runtime.runJob("missing@1", job=job)
        assert returned is job
        assert job.state is JobState.CANCELLED
    finally:
        runtime.close()
