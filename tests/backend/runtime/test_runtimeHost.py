# file: tests/backend/runtime/test_runtimeHost.py ; version: 1
from pathlib import Path

import pytest

from backend.application import ApplicationRunState
from backend.context import CodeEntryIdentity
from backend.registration import RegistrationScope
from backend.runtime.runtimeHost import RuntimeHost
from backend.values import ValueState


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



def test_contexts_share_application_run_authoritative_memory():
    host = RuntimeHost()
    host.start()
    identity = CodeEntryIdentity(
        applicationId=host.applicationRun.application.applicationId,
        applicationRunId=host.applicationRun.applicationRunId,
        packId="test.pack",
        codeEntryId="entry",
        codeEntryInstanceId="entry-instance",
    )

    firstScope = RegistrationScope()
    first = host.createContext(
        identity=identity,
        packRoot=Path.cwd(),
        registrationScope=firstScope,
    )
    transaction = first.memory.openTransaction()
    transaction.set("test/value", {"count": 1})
    transaction.commit()
    first.invalidate()
    firstScope.withdraw()

    secondScope = RegistrationScope()
    second = host.createContext(
        identity=identity,
        packRoot=Path.cwd(),
        registrationScope=secondScope,
    )
    try:
        assert second.memory.load("test/value") == {"count": 1}
        assert second.memory.state("test/value") is ValueState.PRESENT
        assert second.memory.revisionId("test/value") == 1
    finally:
        second.invalidate()
        secondScope.withdraw()
        host.stop()
