# file: tests/backend/runtime/test_applicationOperations.py ; version: 1
import json
from pathlib import Path

from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime import ApplicationRuntimeOperations
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
                "codeEntries": [{"id": "application", "source": "codeEntry.py"}],
            },
        ),
        encoding="utf-8",
    )
    (directory / "codeEntry.py").write_text(
        "def _append(ctx, event):\n"
        "    current = ctx.memory.load('lifecycle/events')\n"
        "    if not isinstance(current, list):\n"
        "        current = []\n"
        "    transaction = ctx.memory.openTransaction()\n"
        "    transaction.set('lifecycle/events', [*current, event])\n"
        "    transaction.commit()\n\n"
        "def onApplicationCreate(ctx, state):\n"
        "    _append(ctx, 'create')\n\n"
        "def onApplicationLoad(ctx, state):\n"
        "    _append(ctx, 'load')\n\n"
        "def onApplicationRun(ctx, state):\n"
        "    _append(ctx, 'run')\n",
        encoding="utf-8",
    )


def test_shared_application_runtime_operations_create_and_load_same_durable_application(
    tmp_path: Path,
):
    packsRoot = tmp_path / "packs"
    packsRoot.mkdir()
    _writeAppPack(packsRoot)

    store = ApplicationStore(tmp_path / "saves")
    operations = ApplicationRuntimeOperations(
        applicationStore=store,
        packResolver=PackResolver(roots=(packsRoot,)),
    )
    plan = ManualActivationPlan(packIds=("test.app",))

    first = operations.createApplication(
        appPackId="test.app",
        plan=plan,
    )
    applicationId = first.applicationId
    firstRunId = first.applicationRunId

    assert first.runtime.applicationRun.application.committedState.load(
        "lifecycle/events",
    ) == ["create", "load", "run"]

    first.close()

    loadedBeforeSecondRun = store.load(
        appPackId="test.app",
        applicationId=applicationId,
    )
    assert loadedBeforeSecondRun.bundle.restoreCommittedState().load(
        "lifecycle/events",
    ) == ["create", "load"]

    second = operations.loadApplication(
        appPackId="test.app",
        applicationId=applicationId,
        plan=plan,
    )

    assert second.applicationId == applicationId
    assert second.applicationRunId != firstRunId
    assert second.loadedSave is not None
    assert second.runtime.applicationRun.application.committedState.load(
        "lifecycle/events",
    ) == ["create", "load", "load", "run"]

    second.close()

    loadedAfterSecondRun = store.load(
        appPackId="test.app",
        applicationId=applicationId,
    )
    assert loadedAfterSecondRun.bundle.restoreCommittedState().load(
        "lifecycle/events",
    ) == ["create", "load", "load"]
