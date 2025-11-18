# backend/content/packs.py
from __future__ import annotations
import re
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

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
_KNOWN_KINDS = frozenset((*PACK_KIND_DIRS.keys(), "savePack"))

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
    manifest: PackManifest | None = None



def _readManifest(dirpath: Path, *, expectedKind: str | None = None) -> PackManifest | None:
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
            kind = str(raw.get("type") or raw.get("kind") or "").strip()
            if not kind:
                return None
            if kind not in _KNOWN_KINDS:
                return None
            if expectedKind and kind != expectedKind:
                return None
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
    


@dataclass(frozen=True)
class _DiscoveryRoot:
    path: Path
    kindHint: str | None
    index: int


class PackResolver:
    """
    Scans content roots (first-party/, third-party/, custom/) for content directories,
    reads manifest, and resolved by author/id/version with precedence by roots order.
    """
    def listPacks(
        self,
        *,
        kinds: set[str] | None = None,
        overrides: Mapping[str, Iterable[Path]] | None = None,
    ) -> list[ResolvedPack]:
        tempOut: list[tuple[int, str, ResolvedPack]] = []
        seen: set[tuple[str, str | None, str, Path]] = set() # (id, author, version, rootDir)
        rootsService = getRootsService()

        discoveryRoots: list[_DiscoveryRoot] = []

        def addDiscoveryRoot(path: Path, *, kindHint: str | None, index: int) -> None:
            try:
                resolved = path.resolve()
            except Exception:
                return
            if not resolved.exists() or not resolved.is_dir():
                return
            discoveryRoots.append(_DiscoveryRoot(path=resolved, kindHint=kindHint, index=index))

        order = 0
        for baseIndex, root in enumerate(rootsService.contentRoots()):
            addDiscoveryRoot(root, kindHint=None, index=order + baseIndex)

        order = len(discoveryRoots)
        if overrides:
            for kind, paths in overrides.items():
                if kind not in PACK_KIND_DIRS:
                    continue
                if kinds and kind not in kinds:
                    continue
                for overridePath in paths:
                    addDiscoveryRoot(Path(overridePath), kindHint=kind, index=order)
                    order += 1

        def findManifestPath(child: Path) -> Path:
            for path in ((child / name) for name in _MANIFEST_NAMES):
                if path.is_file():
                    return path
            raise FileNotFoundError(f"No manifest found in {child}")

        for discoveryRoot in discoveryRoots:
            try:
                for packDir, manifest in self._iterPackDirs(discoveryRoot, kinds):
                    key = (manifest.id, manifest.author, manifest.version, packDir.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    resolvedPack = ResolvedPack(
                        id=manifest.id,
                        name=manifest.name,
                        author=manifest.author,
                        version=manifest.version,
                        kind=manifest.kind,
                        rootDir=packDir.resolve(),
                        manifestPath=findManifestPath(packDir).resolve(),
                        manifest=manifest,
                    )
                    tempOut.append((discoveryRoot.index, packDir.name.lower(), resolvedPack))
            except Exception:
                continue

        tempOut.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in tempOut]

    def _iterPackDirs(
        self,
        root: _DiscoveryRoot,
        kinds: set[str] | None,
    ) -> Iterable[tuple[Path, PackManifest]]:
        targets: list[tuple[str, Path]] = []
        if root.kindHint:
            if kinds and root.kindHint not in kinds:
                return []
            targets.append((root.kindHint, root.path))
        else:
            for kind, subdir in PACK_KIND_DIRS.items():
                if kinds and kind not in kinds:
                    continue
                targets.append((kind, root.path / subdir))

        for kind, searchDir in targets:
            try:
                resolvedSearch = searchDir.resolve()
            except Exception:
                continue
            if not resolvedSearch.exists() or not resolvedSearch.is_dir():
                continue
            try:
                children = list(resolvedSearch.iterdir())
            except Exception:
                continue
            for child in children:
                try:
                    if not child.is_dir():
                        continue
                    if child.is_symlink():
                        # Symlinks are not followed for packs for safety
                        continue
                    childResolved = child.resolve()
                    if not childResolved.is_relative_to(resolvedSearch):
                        continue
                    manifest = _readManifest(childResolved, expectedKind=kind)
                    if not manifest:
                        continue
                    yield childResolved, manifest
                except Exception:
                    continue

    def resolveAppPack(
        self,
        qidOrId: str,
        *,
        overrides: Mapping[str, Iterable[Path]] | None = None,
    ) -> ResolvedPack | None:
        return self.resolvePack(qidOrId, kinds={"appPack"}, overrides=overrides)

    def resolvePack(
        self,
        qidOrId: str,
        *,
        kinds: set[str],
        overrides: Mapping[str, Iterable[Path]] | None = None,
    ) -> ResolvedPack | None:
        if not kinds:
            raise ValueError("kinds must contain at least one pack type")
        """
        Resolve by:
          - exact author@id:version
          - or "id:version"
          - or "author@id"
          - or "id" (select latest by version if comparable, otherwise first by roots precedence)
        """
        author, packId, version, _rest = parseQualifiedPackId(qidOrId)
        candidates = [
            pack for pack in self.listPacks(kinds=kinds, overrides=overrides)
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
