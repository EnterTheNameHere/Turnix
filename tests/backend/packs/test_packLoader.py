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
