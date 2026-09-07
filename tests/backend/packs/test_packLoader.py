# file: tests/backend/packs/test_packLoader.py ; version: 3
import json
from pathlib import Path

import pytest

from backend.packs.runtime import ManualActivationPlan, PackLoader, PackResolver
from backend.runtime.runtimeHost import RuntimeHost
from backend.values import MISSING


def _writePack(root: Path, packId: str, code: str, *, kind: str = "modPack") -> None:
    directory = root / packId.replace(".", "_")
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "packId": packId,
                "kind": kind,
                "version": "0.0.0",
                "codeEntries": [{"id": "entry", "source": "codeEntry.py"}],
            },
        ),
        encoding="utf-8",
    )
    (directory / "codeEntry.py").write_text(code, encoding="utf-8")


def test_plan_failure_rolls_back_packs_activated_by_that_plan(tmp_path: Path):
    _writePack(
        tmp_path,
        "test.first",
        "def onLoad(ctx):\n"
        "    ctx.capabilities.register('test.first@1', lambda ctx, payload: 'first')\n",
    )
    _writePack(
        tmp_path,
        "test.broken",
        "def onLoad(ctx):\n"
        "    ctx.capabilities.register('test.broken@1', lambda ctx, payload: 'broken')\n"
        "    raise RuntimeError('intentional failure')\n",
    )

    host = RuntimeHost(appPackId="test.app")
    host.start()
    loader = PackLoader(host=host, resolver=PackResolver(roots=(tmp_path,)))
    try:
        with pytest.raises(RuntimeError, match="intentional failure"):
            loader.activate(ManualActivationPlan(packIds=("test.first", "test.broken")))

        with pytest.raises(LookupError):
            host.capabilities.resolve("test.first@1")
        with pytest.raises(LookupError):
            host.capabilities.resolve("test.broken@1")
    finally:
        loader.close()
        host.stop()


def test_successful_pack_is_visible_until_loader_close(tmp_path: Path):
    _writePack(
        tmp_path,
        "test.good",
        "def onLoad(ctx):\n"
        "    ctx.capabilities.register('test.good@1', lambda ctx, payload: 'ok')\n",
    )

    host = RuntimeHost(appPackId="test.app")
    host.start()
    loader = PackLoader(host=host, resolver=PackResolver(roots=(tmp_path,)))
    try:
        loader.activate(ManualActivationPlan(packIds=("test.good",)))
        assert host.invokeCapability("test.good@1") == "ok"
        loader.close()
        with pytest.raises(LookupError):
            host.capabilities.resolve("test.good@1")
    finally:
        loader.close()
        host.stop()


def test_failing_onload_is_best_effort_unloaded_with_none_state(tmp_path: Path):
    marker = tmp_path / "cleanup.txt"
    _writePack(
        tmp_path,
        "test.cleanup",
        "def onLoad(ctx):\n"
        "    raise RuntimeError('load exploded')\n\n"
        "def onUnload(ctx, state):\n"
        f"    ctx.io.writeTextAtomic({str(marker)!r}, repr(state))\n",
    )

    host = RuntimeHost(appPackId="test.app")
    host.start()
    loader = PackLoader(host=host, resolver=PackResolver(roots=(tmp_path,)))
    try:
        with pytest.raises(RuntimeError, match="load exploded"):
            loader.activate(ManualActivationPlan(packIds=("test.cleanup",)))
        assert marker.read_text(encoding="utf-8") == "None"
    finally:
        loader.close()
        host.stop()



def test_code_entry_implementation_identity_tracks_exact_executed_source(tmp_path: Path):
    source = (
        "def onLoad(ctx):\n"
        "    ctx.capabilities.register(" 
        "'test.identity@1', "
        "lambda ctx, payload: ctx.identity.producerSnapshot())\n"
    )
    _writePack(tmp_path, "test.identity", source)

    host = RuntimeHost(appPackId="test.app")
    host.start()
    resolver = PackResolver(roots=(tmp_path,))
    loader = PackLoader(host=host, resolver=resolver)
    try:
        loader.activate(ManualActivationPlan(packIds=("test.identity",)))
        first = host.invokeCapability("test.identity@1")
        loader.close()

        loader.activate(ManualActivationPlan(packIds=("test.identity",)))
        second = host.invokeCapability("test.identity@1")
        loader.close()

        assert first == second
        assert first["packId"] == "test.identity"
        assert first["packVersion"] == "0.0.0"
        assert first["sourceSha256"]
        assert first["implementationId"]

        directory = tmp_path / "test_identity"
        (directory / "codeEntry.py").write_text(
            "# implementation changed\n" + source,
            encoding="utf-8",
        )

        loader.activate(ManualActivationPlan(packIds=("test.identity",)))
        third = host.invokeCapability("test.identity@1")

        assert third["sourceSha256"] != first["sourceSha256"]
        assert third["implementationId"] != first["implementationId"]
    finally:
        loader.close()
        host.stop()

def test_non_app_pack_cannot_declare_application_lifecycle_hook(tmp_path: Path):
    _writePack(
        tmp_path,
        "test.mod",
        "def onApplicationLoad(ctx, state):\n"
        "    pass\n",
    )

    host = RuntimeHost(appPackId="test.app")
    host.start()
    loader = PackLoader(host=host, resolver=PackResolver(roots=(tmp_path,)))
    try:
        with pytest.raises(ValueError, match="Non-appPack.*onApplicationLoad"):
            loader.activate(ManualActivationPlan(packIds=("test.mod",)))
    finally:
        loader.close()
        host.stop()


def test_application_lifecycle_requires_completed_activation_plan(tmp_path: Path):
    _writePack(
        tmp_path,
        "test.app",
        "def onApplicationLoad(ctx, state):\n"
        "    pass\n",
        kind="appPack",
    )

    host = RuntimeHost(appPackId="test.app")
    host.start()
    resolver = PackResolver(roots=(tmp_path,))
    loader = PackLoader(host=host, resolver=resolver)
    try:
        loader.activatePack(resolver.requireSingle("test.app"))
        with pytest.raises(RuntimeError, match="activation-plan barrier"):
            loader.invokeApplicationLoad()
    finally:
        loader.close()
        host.stop()


def test_application_load_runs_only_after_all_pack_onloads_and_receives_code_entry_state(
    tmp_path: Path,
):
    _writePack(
        tmp_path,
        "test.app",
        "def onLoad(ctx):\n"
        "    return {'loaded': True}\n\n"
        "def onApplicationLoad(ctx, state):\n"
        "    dependency = ctx.capabilities.call('test.dependency@1')\n"
        "    transaction = ctx.memory.openTransaction()\n"
        "    transaction.set('lifecycle/load', {'state': state, 'dependency': dependency})\n"
        "    transaction.commit()\n",
        kind="appPack",
    )
    _writePack(
        tmp_path,
        "test.dependency",
        "def onLoad(ctx):\n"
        "    ctx.capabilities.register('test.dependency@1', lambda ctx, payload: 'ready')\n",
    )

    host = RuntimeHost(appPackId="test.app")
    host.start()
    loader = PackLoader(host=host, resolver=PackResolver(roots=(tmp_path,)))
    try:
        loader.activate(
            ManualActivationPlan(packIds=("test.app", "test.dependency")),
        )

        assert host.applicationRun.application.committedState.load("lifecycle/load") is MISSING

        loader.invokeApplicationLoad()

        assert host.applicationRun.application.committedState.load("lifecycle/load") == {
            "state": {"loaded": True},
            "dependency": "ready",
        }
    finally:
        loader.close()
        host.stop()


def test_application_create_changes_remain_under_supplied_outer_transaction(tmp_path: Path):
    _writePack(
        tmp_path,
        "test.app",
        "def onApplicationCreate(ctx, state):\n"
        "    transaction = ctx.memory.openTransaction()\n"
        "    transaction.set('lifecycle/create', {'accepted': True})\n"
        "    transaction.commit()\n",
        kind="appPack",
    )

    host = RuntimeHost(appPackId="test.app")
    host.start()
    loader = PackLoader(host=host, resolver=PackResolver(roots=(tmp_path,)))
    root = host.applicationRun.application.committedState
    outer = root.openTransaction()
    try:
        loader.activate(ManualActivationPlan(packIds=("test.app",)))
        loader.invokeApplicationCreate(memoryView=outer)

        assert outer.load("lifecycle/create") == {"accepted": True}
        assert root.load("lifecycle/create") is MISSING

        outer.abort()
        assert root.load("lifecycle/create") is MISSING
    finally:
        loader.close()
        host.stop()

