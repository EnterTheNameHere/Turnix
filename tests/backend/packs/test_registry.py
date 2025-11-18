import json5
from pathlib import Path

import pytest

from backend.content.packs import PackResolver
from backend.content.roots import RootsService
from backend.packs.registry import PackRegistry
from backend.packs.savepack import SavePackManager


@pytest.fixture()
def temp_roots(monkeypatch, tmp_path):
    base = tmp_path / "turnix"
    service = RootsService.build(cliRoot=str(base))
    monkeypatch.setattr("backend.app.globals.getRootsService", lambda: service)
    monkeypatch.setattr("backend.content.packs.getRootsService", lambda: service)
    monkeypatch.setattr("backend.content.saves.getRootsService", lambda: service)
    return base, service


def write_manifest(dir_path: Path, payload: dict) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "manifest.json5").write_text(json5.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_pack_resolver_discovers_from_first_party(temp_roots):
    base, _service = temp_roots
    app_dir = base / "first-party" / "appPacks" / "ai-chat"
    write_manifest(
        app_dir,
        {
            "id": "ai-chat",
            "name": "AI Chat",
            "version": "1.0.0",
            "type": "appPack",
            "meta": {"viewPacks": ["ui.default"]},
        },
    )

    resolver = PackResolver()
    packs = resolver.listPacks(kinds={"appPack"})
    assert len(packs) == 1
    assert packs[0].id == "ai-chat"
    assert packs[0].manifest.meta["viewPacks"] == ["ui.default"]


def test_registry_loads_app_pack_with_custom_entrypoint(temp_roots):
    base, _service = temp_roots
    app_dir = base / "first-party" / "appPacks" / "story-core"
    write_manifest(
        app_dir,
        {
            "id": "story-core",
            "name": "Story",
            "version": "2.0.0",
            "type": "appPack",
            "meta": {
                "runtime": {
                    "entrypoint": "tests.backend.packs.runtime_helpers:make_runtime",
                    "init": {"foo": 1},
                },
                "mods": ["Turnix@llm"],
            },
        },
    )

    registry = PackRegistry()
    loaded = registry.loadByQualifiedId("story-core", kind="appPack")
    runtime = loaded.createRuntime(
        runtimeId="demo",
        configService=object(),
        configRegistry=object(),
        globalConfigView=object(),
    )
    assert runtime["runtimeId"] == "demo"
    assert loaded.dependencies["mods"] == ["Turnix@llm"]


def test_save_pack_override_takes_precedence(temp_roots):
    base, _service = temp_roots
    app_dir = base / "first-party" / "appPacks" / "tiny-app"
    write_manifest(
        app_dir,
        {
            "id": "tiny-app",
            "name": "Tiny",
            "version": "1.0.0",
            "type": "appPack",
        },
    )

    resolver = PackResolver()
    pack = resolver.resolveAppPack("tiny-app")
    assert pack is not None

    saveMgr = SavePackManager()
    binding = saveMgr.bind("tiny-app", "slot-a", create=True)
    copied = saveMgr.copyPackIntoSave(binding, pack)

    overrides = saveMgr.overridesForBinding(binding)
    assert overrides["appPack"][0] == binding.saveDir / "packs" / "appPacks"

    # Mutate the copied manifest to make version differ
    copied_manifest = copied / "manifest.json5"
    data = json5.loads(copied_manifest.read_text(encoding="utf-8"))
    data["version"] = "2.0.0"
    copied_manifest.write_text(json5.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    pack2 = resolver.resolvePack("tiny-app", kinds={"appPack"}, overrides=overrides)
    assert pack2 is not None
    assert pack2.manifest.version == "2.0.0"
    assert str(pack2.rootDir).startswith(str(binding.saveDir))
