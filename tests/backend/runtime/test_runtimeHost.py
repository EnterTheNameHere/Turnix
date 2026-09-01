from pathlib import Path

import pytest

from backend.application import ApplicationRunState
from backend.context import CodeEntryIdentity
from backend.registration import RegistrationScope
from backend.runtime.runtimeHost import RuntimeHost


class RaisingTracer:
    def emitEvent(self, **_kwargs):
        raise RuntimeError("trace destination failed")

    def close(self):
        raise RuntimeError("trace close failed")


def test_application_run_is_non_restartable_and_requires_active_work():
    host = RuntimeHost()
    assert host.applicationRun.state is ApplicationRunState.CREATED

    with pytest.raises(RuntimeError):
        host.runJob("missing@1")

    host.start()
    assert host.applicationRun.state is ApplicationRunState.ACTIVE
    host.stop()
    assert host.applicationRun.state is ApplicationRunState.STOPPED

    with pytest.raises(RuntimeError):
        host.start()


def test_runtime_config_is_detached_from_caller_and_public_snapshots():
    source = {"nested": {"value": 1}}
    host = RuntimeHost(config=source)
    source["nested"]["value"] = 2
    assert host.config == {"nested": {"value": 1}}

    snapshot = host.config
    snapshot["nested"]["value"] = 3
    assert host.config == {"nested": {"value": 1}}


def test_non_activation_context_rejects_registration():
    host = RuntimeHost()
    host.start()
    identity = CodeEntryIdentity(
        applicationId=host.applicationRun.application.applicationId,
        applicationRunId=host.applicationRun.applicationRunId,
        packId="test.pack",
        codeEntryId="entry",
        codeEntryInstanceId="entry-instance",
    )
    scope = RegistrationScope()
    context = host.createContext(identity=identity, packRoot=Path.cwd(), registrationScope=scope)
    try:
        with pytest.raises(RuntimeError):
            context.capabilities.register("test.capability@1", lambda ctx, payload: None)
    finally:
        context.invalidate()
        scope.withdraw()
        host.stop()


def test_trace_publication_failure_does_not_change_runtime_lifecycle():
    host = RuntimeHost(tracer=RaisingTracer())

    host.start()
    assert host.applicationRun.state is ApplicationRunState.ACTIVE
    assert host.trace("test") is False

    host.stop()
    assert host.applicationRun.state is ApplicationRunState.STOPPED
