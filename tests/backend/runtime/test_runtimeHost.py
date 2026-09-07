# file: tests/backend/runtime/test_runtimeHost.py ; version: 2
import json
from pathlib import Path

import pytest

from backend.application import ApplicationRunState
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime import RuntimeHost, RuntimeHostState
from backend.save import ApplicationStore


def _writeAppPack(root: Path) -> None:
    directory = root / "test_app"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "packId": "test.app",
                "kind": "appPack",
                "version": "0.0.0",
                "codeEntries": [],
            },
        ),
        encoding="utf-8",
    )


def _host(tmp_path: Path) -> RuntimeHost:
    packsRoot = tmp_path / "packs"
    packsRoot.mkdir(parents=True)
    _writeAppPack(packsRoot)
    return RuntimeHost(
        applicationStore=ApplicationStore(tmp_path / "saves"),
        packResolver=PackResolver(roots=(packsRoot,)),
    )


def _plan() -> ManualActivationPlan:
    return ManualActivationPlan(packIds=("test.app",))


def test_runtime_host_exists_with_zero_application_runtimes(tmp_path: Path):
    host = _host(tmp_path)

    assert host.state is RuntimeHostState.CREATED
    assert host.activeApplicationRuntimes == ()

    host.start()

    assert host.state is RuntimeHostState.ACTIVE
    assert host.activeApplicationRuntimes == ()

    host.stop()

    assert host.state is RuntimeHostState.STOPPED


def test_runtime_host_identity_is_stable_and_distinct_per_host(tmp_path: Path):
    first = _host(tmp_path / "first")
    second = _host(tmp_path / "second")

    assert first.runtimeHostId
    assert second.runtimeHostId
    assert first.runtimeHostId != second.runtimeHostId
    assert first.runtimeHostId == first.runtimeHostId


def test_runtime_host_owns_multiple_independent_application_runtimes(tmp_path: Path):
    host = _host(tmp_path)
    host.start()

    first = host.createApplication(
        appPackId="test.app",
        plan=_plan(),
    )
    second = host.createApplication(
        appPackId="test.app",
        plan=_plan(),
    )

    assert first is not second
    assert first.applicationRun.application.applicationId != second.applicationRun.application.applicationId
    assert first.applicationRun.applicationRunId != second.applicationRun.applicationRunId
    assert first.applicationRun.state is ApplicationRunState.ACTIVE
    assert second.applicationRun.state is ApplicationRunState.ACTIVE
    assert host.applicationRuntime(first.applicationRun.applicationRunId) is first
    assert host.applicationRuntime(second.applicationRun.applicationRunId) is second
    assert host.activeApplicationRuntimes == (first, second)

    firstTransaction = first.applicationRun.application.committedState.openTransaction()
    firstTransaction.set("test/value", "first")
    firstTransaction.commit()

    assert first.applicationRun.application.committedState.load("test/value") == "first"
    assert second.applicationRun.application.committedState.revisionId("test/value") == 0

    host.closeApplicationRun(first.applicationRun.applicationRunId)

    assert first.applicationRun.state is ApplicationRunState.STOPPED
    assert second.applicationRun.state is ApplicationRunState.ACTIVE
    assert host.activeApplicationRuntimes == (second,)

    with pytest.raises(LookupError, match="not active"):
        host.applicationRuntime(first.applicationRun.applicationRunId)

    host.stop()

    assert second.applicationRun.state is ApplicationRunState.STOPPED
    assert host.activeApplicationRuntimes == ()
    assert host.state is RuntimeHostState.STOPPED


def test_runtime_host_rejects_operations_until_started_and_after_stopped(tmp_path: Path):
    host = _host(tmp_path)

    with pytest.raises(RuntimeError, match="not active"):
        host.createApplication(
            appPackId="test.app",
            plan=_plan(),
        )

    host.start()
    host.stop()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        host.start()

    with pytest.raises(RuntimeError, match="not active"):
        host.createApplication(
            appPackId="test.app",
            plan=_plan(),
        )


def test_runtime_host_rejects_second_active_run_of_same_application(tmp_path: Path):
    host = _host(tmp_path)
    host.start()

    first = host.createApplication(
        appPackId="test.app",
        plan=_plan(),
    )
    applicationId = first.applicationRun.application.applicationId

    with pytest.raises(RuntimeError, match="already active"):
        host.loadApplication(
            appPackId="test.app",
            applicationId=applicationId,
            plan=_plan(),
        )

    host.closeApplicationRun(first.applicationRun.applicationRunId)

    second = host.loadApplication(
        appPackId="test.app",
        applicationId=applicationId,
        plan=_plan(),
    )

    assert second.applicationRun.application.applicationId == applicationId
    assert second.applicationRun.applicationRunId != first.applicationRun.applicationRunId

    host.stop()


def test_runtime_host_stop_is_idempotent(tmp_path: Path):
    host = _host(tmp_path)
    host.start()
    runtime = host.createApplication(
        appPackId="test.app",
        plan=_plan(),
    )

    host.stop()
    host.stop()

    assert runtime.applicationRun.state is ApplicationRunState.STOPPED
    assert host.state is RuntimeHostState.STOPPED
