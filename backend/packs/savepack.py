# backend/packs/savepack.py
from __future__ import annotations
import json5
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.content.packs import PACK_KIND_DIRS, ResolvedPack
from backend.content.saves import SaveBinding, SaveManager

__all__ = [
    "SavePackManifest",
    "SavePackManager",
]

_SAVE_PACK_DIR = "packs"
_PACK_MANIFEST = "pack-manifest.json5"


def _defaultManifest(binding: SaveBinding) -> dict[str, Any]:
    return {
        "appPackId": binding.appPackId,
        "runtimeInstanceId": binding.instanceId,
        "packs": [],
    }


@dataclass(slots=True)
class SavePackManifest:
    appPackId: str
    runtimeInstanceId: str
    packs: list[dict[str, Any]]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SavePackManifest":
        appPackId = str(raw.get("appPackId") or "").strip()
        runtimeInstanceId = str(raw.get("runtimeInstanceId") or "").strip()
        packsRaw = raw.get("packs")
        packs: list[dict[str, Any]] = []
        if isinstance(packsRaw, Iterable) and not isinstance(packsRaw, (str, bytes)):
            for entry in packsRaw:
                if isinstance(entry, Mapping):
                    packs.append({
                        "id": str(entry.get("id") or "").strip(),
                        "kind": str(entry.get("kind") or "").strip(),
                        "version": str(entry.get("version") or "").strip(),
                        "path": str(entry.get("path") or "").strip(),
                    })
        return cls(appPackId=appPackId, runtimeInstanceId=runtimeInstanceId, packs=packs)


class SavePackManager:
    """
    Coordinates copying packs into a save directory and exposes override roots
    so PackResolver can prioritize those copies during loading.
    """

    def __init__(self, saveManager: SaveManager | None = None) -> None:
        self._saveManager = saveManager or SaveManager()

    # ----- Override roots -----

    def overridesFor(self, saveDir: Path) -> dict[str, list[Path]]:
        base = saveDir / _SAVE_PACK_DIR
        overrides: dict[str, list[Path]] = {}
        for kind, subdir in PACK_KIND_DIRS.items():
            target = base / subdir
            if target.exists() and target.is_dir():
                overrides.setdefault(kind, []).append(target)
        return overrides

    # ----- Manifest helpers -----

    def loadManifest(self, saveDir: Path) -> SavePackManifest | None:
        manifestPath = saveDir / _PACK_MANIFEST
        if not manifestPath.exists():
            return None
        data = json5.loads(manifestPath.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise TypeError(f"Save pack manifest '{manifestPath}' must be an object")
        return SavePackManifest.from_dict(data)

    def writeManifest(self, binding: SaveBinding, manifest: Mapping[str, Any]) -> Path:
        manifestPath = binding.saveDir / _PACK_MANIFEST
        manifestPath.parent.mkdir(parents=True, exist_ok=True)
        manifestPath.write_text(json5.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifestPath

    # ----- Pack copying -----

    def copyPackIntoSave(
        self,
        binding: SaveBinding,
        pack: ResolvedPack,
        *,
        overwrite: bool = True,
    ) -> Path:
        if pack.kind not in PACK_KIND_DIRS:
            raise ValueError(f"Cannot copy pack kind '{pack.kind}' into save")
        base = binding.saveDir / _SAVE_PACK_DIR / PACK_KIND_DIRS[pack.kind]
        base.mkdir(parents=True, exist_ok=True)
        destination = base / pack.rootDir.name
        if destination.exists() and overwrite:
            shutil.rmtree(destination)
        shutil.copytree(pack.rootDir, destination)

        manifest = self.loadManifest(binding.saveDir)
        if manifest is None:
            manifestDict = _defaultManifest(binding)
        else:
            manifestDict = {
                "appPackId": manifest.appPackId or binding.appPackId,
                "runtimeInstanceId": manifest.runtimeInstanceId or binding.instanceId,
                "packs": manifest.packs,
            }

        manifestPacks = [entry for entry in manifestDict.get("packs", []) if entry.get("id") != pack.id or entry.get("kind") != pack.kind]
        manifestPacks.append({
            "id": pack.id,
            "kind": pack.kind,
            "version": pack.version,
            "path": str(destination.relative_to(binding.saveDir)),
        })
        manifestDict["packs"] = manifestPacks
        self.writeManifest(binding, manifestDict)
        return destination

    # ----- Convenience -----

    def bind(self, appPackId: str, instanceId: str, *, create: bool = False) -> SaveBinding:
        return self._saveManager.bind(appPackId, instanceId, create=create)

    def overridesForBinding(self, binding: SaveBinding) -> dict[str, list[Path]]:
        return self.overridesFor(binding.saveDir)
