# file: tests/backend/packs/test_packLoader.py ; version: 1
import json
from pathlib import Path

import pytest

from backend.packs.runtime import ManualActivationPlan, PackLoader, PackResolver
from backend.runtime.runtimeHost import RuntimeHost


def _writePack(root: Path, packId: str, code: str) -> None:
    directory = root / packId.replace(".", "_")
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "packId": packId,
                "kind": "modPack",
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

    host = RuntimeHost()
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

    host = RuntimeHost()
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

    host = RuntimeHost()
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

    host = RuntimeHost()
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
