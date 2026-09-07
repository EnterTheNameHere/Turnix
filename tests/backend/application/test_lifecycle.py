# file: tests/backend/application/test_lifecycle.py ; version: 3
import json
from pathlib import Path

import pytest

from backend.application import ApplicationLifecycle, ApplicationRunState
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.application.applicationRuntime import ApplicationRuntime
from backend.save import ApplicationStore
from backend.values import MISSING


def _writeAppPack(root: Path, code: str) -> None:
    directory = root / "test_app"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "packId": "test.app",
                "kind": "appPack",
                "version": "0.0.0",
                "codeEntries": [{"id": "application", "source": "codeEntry.py"}],
            },
        ),
        encoding="utf-8",
    )
    (directory / "codeEntry.py").write_text(code, encoding="utf-8")



def _plan() -> ManualActivationPlan:
    return ManualActivationPlan(packIds=("test.app",))


def test_create_and_load_follow_persistent_application_lifecycle_order(tmp_path: Path):
    packsRoot = tmp_path / "packs"
    packsRoot.mkdir()
    _writeAppPack(
        packsRoot,
        "def _append(ctx, event):\n"
        "    current = ctx.memory.load('lifecycle/events')\n"
        "    if not isinstance(current, list):\n"
        "        current = []\n"
        "    transaction = ctx.memory.openTransaction()\n"
        "    transaction.set('lifecycle/events', [*current, event])\n"
        "    transaction.commit()\n\n"
        "def onLoad(ctx):\n"
        "    return {'codeEntryState': 'ready'}\n\n"
        "def onApplicationCreate(ctx, state):\n"
        "    assert state == {'codeEntryState': 'ready'}\n"
        "    _append(ctx, 'create')\n\n"
        "def onApplicationLoad(ctx, state):\n"
        "    assert state == {'codeEntryState': 'ready'}\n"
        "    _append(ctx, 'load')\n\n"
        "def onApplicationRun(ctx, state):\n"
        "    assert state == {'codeEntryState': 'ready'}\n"
        "    _append(ctx, 'run')\n",
    )

    store = ApplicationStore(tmp_path / "saves")
    firstHost = ApplicationRuntime(appPackId="test.app", applicationStore=store, packResolver=PackResolver(roots=(packsRoot,)))
    firstApplicationId = firstHost.applicationRun.application.applicationId
    firstRunId = firstHost.applicationRun.applicationRunId
    
    accepted = ApplicationLifecycle.create(
        runtime=firstHost,
        plan=_plan(),
    )

    assert firstHost.applicationRun.state is ApplicationRunState.ACTIVE
    assert accepted.generation == 2
    assert firstHost.applicationRun.application.committedState.load("lifecycle/events") == [
        "create",
        "load",
        "run",
    ]

    durableAfterCreate = store.load(
        appPackId="test.app",
        applicationId=firstApplicationId,
    )
    assert durableAfterCreate.bundle.generation == 2
    assert durableAfterCreate.bundle.restoreCommittedState().load("lifecycle/events") == [
        "create",
        "load",
    ]

    firstHost.packLoader.close()
    firstHost.stop()

    secondHost, loaded = ApplicationRuntime.loadApplication(
        applicationStore=store,
        packResolver=PackResolver(roots=(packsRoot,)),
        appPackId="test.app",
        applicationId=firstApplicationId,
    )
    assert loaded.bundle.generation == 2
    assert secondHost.applicationRun.applicationRunId != firstRunId
    
    acceptedAgain = ApplicationLifecycle.load(
        runtime=secondHost,
        plan=_plan(),
    )

    assert secondHost.applicationRun.state is ApplicationRunState.ACTIVE
    assert acceptedAgain.generation == 3
    assert secondHost.applicationRun.application.committedState.load("lifecycle/events") == [
        "create",
        "load",
        "load",
        "run",
    ]

    durableAfterLoad = store.load(
        appPackId="test.app",
        applicationId=firstApplicationId,
    )
    assert durableAfterLoad.bundle.generation == 3
    assert durableAfterLoad.bundle.restoreCommittedState().load("lifecycle/events") == [
        "create",
        "load",
        "load",
    ]

    secondHost.packLoader.close()
    secondHost.stop()


def test_failed_application_create_aborts_root_and_does_not_publish_application(
    tmp_path: Path,
):
    packsRoot = tmp_path / "packs"
    packsRoot.mkdir()
    _writeAppPack(
        packsRoot,
        "def onApplicationCreate(ctx, state):\n"
        "    transaction = ctx.memory.openTransaction()\n"
        "    transaction.set('lifecycle/create', {'mustDisappear': True})\n"
        "    transaction.commit()\n"
        "    raise RuntimeError('creation failed')\n",
    )

    store = ApplicationStore(tmp_path / "saves")
    host = ApplicationRuntime(appPackId="test.app", applicationStore=store, packResolver=PackResolver(roots=(packsRoot,)))
    applicationId = host.applicationRun.application.applicationId
    
    with pytest.raises(RuntimeError, match="creation failed"):
        ApplicationLifecycle.create(
            runtime=host,
            plan=_plan(),
        )

    assert host.applicationRun.state is ApplicationRunState.CREATED
    assert host.applicationRun.application.committedState.load("lifecycle/create") is MISSING
    assert not store.applicationPath(
        appPackId="test.app",
        applicationId=applicationId,
    ).exists()


def test_noop_application_load_does_not_create_redundant_generation(tmp_path: Path):
    packsRoot = tmp_path / "packs"
    packsRoot.mkdir()
    _writeAppPack(
        packsRoot,
        "def onApplicationCreate(ctx, state):\n"
        "    pass\n\n"
        "def onApplicationLoad(ctx, state):\n"
        "    pass\n\n"
        "def onApplicationRun(ctx, state):\n"
        "    pass\n",
    )

    store = ApplicationStore(tmp_path / "saves")
    firstHost = ApplicationRuntime(appPackId="test.app", applicationStore=store, packResolver=PackResolver(roots=(packsRoot,)))
    applicationId = firstHost.applicationRun.application.applicationId
    
    accepted = ApplicationLifecycle.create(
        runtime=firstHost,
        plan=_plan(),
    )
    assert accepted.generation == 1

    firstHost.packLoader.close()
    firstHost.stop()

    secondHost, _loaded = ApplicationRuntime.loadApplication(
        applicationStore=store,
        packResolver=PackResolver(roots=(packsRoot,)),
        appPackId="test.app",
        applicationId=applicationId,
    )
    
    acceptedAgain = ApplicationLifecycle.load(
        runtime=secondHost,
        plan=_plan(),
    )

    assert acceptedAgain.generation == 1
    assert store.load(
        appPackId="test.app",
        applicationId=applicationId,
    ).bundle.generation == 1

    secondHost.packLoader.close()
    secondHost.stop()
