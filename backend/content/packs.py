# backend/content/packs.py
from __future__ import annotations
import re
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json5

from backend.app.globals import getRootsService
from backend.core.schema_registry import SEMVER_PATTERN_RE

__all__ = [
    "PackManifest", "ResolvedPack", "PackResolver",
    "parseQualifiedPackId", "PACK_KIND_DIRS",
]

_MANIFEST_NAMES = ("manifest.json5", "manifest.json")
PACK_KIND_DIRS: Mapping[str, str] = {
    "appPack": "appPacks",
    "viewPack": "viewPacks",
    "contentPack": "contentPacks",
    "mod": "mods",
}
_KNOWN_PACK_KINDS = frozenset((*PACK_KIND_DIRS.keys(), "savePack"))

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_AUTHOR_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Qualified id grammar: author@id:version(:subpath)?
# - author optional (if missing → any author)
# - version optional (if missing → latest)
# - subpath optional (reserved for future filtering)
_QID_RE = re.compile(
    r"^(?:(?P<author>[A-Za-z0-9_-]+)@)?(?P<id>[A-Za-z0-9_-]+)(?::(?P<version>[^\s:\/\\]+))?(?::(?P<rest>.*))?$"
)



@dataclass(frozen=True)
class PackManifest:
    id: str
    name: str
    version: str
    author: str | None = None
    kind: str = "appPack" # "mod", "contentPack", ...
    # Optional metadata bag (languages, images dir, etc.)
    meta: dict[str, Any] | None = None



@dataclass(frozen=True)
class ResolvedPack:
    """
    A fully resolved pack location.
    """
    id: str
    name: str
    version: str
    kind: str
    rootDir: Path       # Content directory root (the "pack directory")
    manifestPath: Path
    author: str | None = None



def _readManifest(dirpath: Path) -> PackManifest | None:
    for filename in _MANIFEST_NAMES:
        path = dirpath / filename
        if not path.exists() or not path.is_file():
            continue
        try:
            if path.suffix == ".json5":
                raw = json5.loads(path.read_text(encoding="utf-8"))
            elif path.suffix == ".json":
                raw = json.loads(path.read_text(encoding="utf-8"))
            else:
                raise ValueError(f"Unknown manifest file extension '{path.suffix}'")
            if raw is None or not isinstance(raw, dict):
                raise ValueError(f"Manifest file '{path}' is not a JSON object")

            packId = str(raw.get("id") or "").strip()
            name = str(raw.get("name") or packId or "").strip()
            version = str(raw.get("version") or "").strip()
            author = raw.get("author")
            kind = str(raw.get("kind") or "appPack").strip()
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            
            if not packId or not _ID_RE.fullmatch(packId):
                return None
            if author is not None and (not isinstance(author, str) or not _AUTHOR_RE.fullmatch(author)):
                author = None
            if not version or not SEMVER_PATTERN_RE.match(version):
                # Allow non-semver now
                pass
            return PackManifest(
                id=packId,
                name=name or packId,
                version=version,
                author=author,
                kind=kind,
                meta=meta,
            )
        except Exception:
            return None
    return None
            


def parseQualifiedPackId(qid: str) -> tuple[str | None, str, str | None, str | None]:
    """
    Parses "author@id:version:rest" → (author|None, id, version|None, rest|None)
    This stub intentionally does not interpret :rest beyond capturing it.
    """
    matched = _QID_RE.match((qid or "").strip())
    if not matched:
        raise ValueError(f"Invalid qualified pack id: '{qid}'")
    author = matched.group("author")
    packId = matched.group("id")
    version = matched.group("version")
    rest = matched.group("rest")
    return author, packId, version, rest
    


class PackResolver:
    """
    Scans content roots (assets/, downloaded/, custom/) for content directories,
    reads manifest, and resolved by author/id/version with precedence by roots order.
    """
    def listPacks(self, *, kinds: set[str] | None = None) -> list[ResolvedPack]:
        tempOut: list[tuple[int, str, ResolvedPack]] = []
        seen: set[tuple[str, str | None, str, Path]] = set() # (id, author, version, rootDir)
        rootsService = getRootsService()
        
        def findManifestPath(child: Path) -> Path:
            for path in ((child / name) for name in _MANIFEST_NAMES):
                if path.is_file():
                    return path
            raise FileNotFoundError(f"No manifest found in {child}")
        
        # Iterate roots in precedence order and remember their index.
        for rootIndex, root in enumerate(rootsService.contentRoots()):
            try:
                if not root.exists():
                    continue
                for child in root.iterdir():
                    if not child.is_dir():
                        continue
                    manifest = _readManifest(child)
                    if not manifest:
                        continue
                    if kinds and manifest.kind not in kinds:
                        continue
                    key = (manifest.id, manifest.author, manifest.version, child.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    resolvedPack = ResolvedPack(
                        id=manifest.id,
                        name=manifest.name,
                        author=manifest.author,
                        version=manifest.version,
                        kind=manifest.kind,
                        rootDir=child.resolve(),
                        manifestPath=findManifestPath(child).resolve(),
                    )
                    # We temporarily stash rootIndex to sort deterministically, then drop.
                    tempOut.append((rootIndex, child.name.lower(), resolvedPack))
            except Exception:
                # Defensive: ignore bad dirs
                continue
        
        # Sort by (root precedence, folder name) to be deterministically but keep precedence.
        tempOut.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in tempOut]

    def resolveAppPack(self, qidOrId: str) -> ResolvedPack | None:
        """
        Resolve by:
          - exact author@id:version
          - or "id:version"
          - or "author@id"
          - or "id" (select latest by version if comparable, otherwise first by roots precedence)
        """
        author, packId, version, _rest = parseQualifiedPackId(qidOrId)
        candidates = [
            pack for pack in self.listPacks(kinds={"appPack"})
            if pack.id == packId and (author is None or pack.author == author)
        ]
        if not candidates:
            return None
        if version:
            # Prefer exact version
            exact = [pack for pack in candidates if pack.version == version]
            if exact:
                return exact[-1] # Last is fine as roots are already precedence-ordered.
            # TODO: add SemVer choice here when ready
            return None
        # No version → pick "latest" by SemVer if possible, else pick last by roots order.
        def semverKey(version: str) -> tuple[int, int, int, int]:
            matched = SEMVER_PATTERN_RE.match(version or "")
            if not matched:
                return (0, 0, 0, 0)
            major = int(matched.group("major"))
            minor = int(matched.group("minor"))
            patch = int(matched.group("patch"))
            isStable = 1 if (matched.group("prerelease") is None) else 0
            return (major, minor, patch, isStable)
        candidates.sort(key=lambda pack: semverKey(pack.version))
        return candidates[-1]
