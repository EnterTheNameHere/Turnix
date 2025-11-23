# backend/content/packs.py
from __future__ import annotations
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json5

from backend.app.globals import configBool, getRootsService
from backend.core.schema_registry import SEMVER_PATTERN_RE

logger = logging.getLogger(__name__)

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

_ID_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
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
    rawJson: dict[str, Any] | None = None
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
    sourceRoot: Path    # The root under which the pack was discovered
    rawJson: dict[str, Any] | None
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
            displayName = str(raw.get("displayName") or "").strip()
            version = str(raw.get("version") or "").strip()
            author = raw.get("author")
            kind = str(raw.get("kind") or "").strip()
            metaRaw = raw.get("meta")
            meta = metaRaw if isinstance(metaRaw, dict) else {}
            
            if "@" in packId:
                parts = packId.split("@", 1)
                if not author:
                    author = parts[0] or None
                elif parts[0] and author != parts[0]:
                    logger.warning(
                        "Author mismatch: manifest declares '%s', but qualified ID uses '%s'. "
                        "Qualified ID takes precedence.",
                        author, parts[0],
                    )
                    author = parts[0]
                
                packId = parts[1] if len(parts) > 1 else packId
            
            if not packId or not _ID_RE.fullmatch(packId):
                raise ValueError(f"Invalid packId: '{packId}'. Manifest path: {str(path)}")
            if author is not None and (not isinstance(author, str) or not _AUTHOR_RE.fullmatch(author)):
                author = None
            if not kind or kind not in _KNOWN_PACK_KINDS:
                raise ValueError(f"kind must be one of {' '.join(_KNOWN_PACK_KINDS)}. Got {kind!r} instead.")
            if not version or not SEMVER_PATTERN_RE.match(version):
                # Allow non-semver now
                pass
            manifest = PackManifest(
                id=packId,
                name=displayName or packId,
                version=version,
                author=author,
                kind=kind,
                meta=meta,
                rawJson=raw,
            )
            return manifest
        except Exception:
            logger.exception("Failed to read manifest file %s", path)
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
        allowSymlinks = configBool("roots.followSymlinks", False)
        
        def findManifestPath(child: Path) -> Path:
            for path in ((child / name) for name in _MANIFEST_NAMES):
                if path.is_file():
                    return path
            raise FileNotFoundError(f"No manifest found in {child}")
        
        for rootIndex, base in enumerate(rootsService.contentRoots()):
            try:
                baseResolved = base.resolve(strict=False)
            except Exception:
                    continue
            
            for packKind, packDirName in PACK_KIND_DIRS.items():
                packRoot = base / packDirName
                if not packRoot.exists() or not packRoot.is_dir():
                    continue
                
                for child in packRoot.iterdir():
                    try:
                        if not child.is_dir():
                            continue
                        if child.is_symlink() and not allowSymlinks:
                            continue
                        resolvedChild = child.resolve(strict=False)
                        # Guard against escaping the discovery root
                        if not resolvedChild.is_relative_to(baseResolved):
                            continue
                    except Exception:
                        continue
                    
                    manifest = _readManifest(child)
                    if not manifest:
                        continue
                    
                    if kinds and manifest.kind not in kinds:
                        continue
                    
                    if manifest.kind != packKind:
                        continue
                    
                    key = (manifest.id, manifest.author, manifest.version, resolvedChild)
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        manifestPath = findManifestPath(child).resolve()
                    except Exception:
                        continue
                    
                    resolvedPack = ResolvedPack(
                        id=manifest.id,
                        name=manifest.name,
                        author=manifest.author,
                        version=manifest.version,
                        kind=manifest.kind,
                        rootDir=resolvedChild,
                        manifestPath=manifestPath,
                        sourceRoot=baseResolved,
                        rawJson=manifest.rawJson,
                    )
                    tempOut.append((rootIndex, child.name.lower(), resolvedPack))
        
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
