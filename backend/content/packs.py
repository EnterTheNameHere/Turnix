# backend/content/packs.py
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.globals import getTracer
from backend.content.pack_meta import (
    PackMeta,
    PackMetaRegistry,
    buildPackMetaRegistry,
)
from backend.semver.semver import (
    SemVerPackVersion,
    SemVerPackRequirement,
    SemVerResolver,
    parseSemVerPackRequirement,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ResolvedPack",
    "PackResolver",
]



@dataclass(frozen=True)
class ResolvedPack:
    """
    Adapter over PackMeta.
    """
    id: str
    name: str
    # Declared version string, or None if not specified in manifest.
    version: str | None
    # Manifest-declared kind ("appPack", "viewPack", "contentPack", "mod", "savePack")
    kind: str
    # The pack root directory.
    rootDir: Path
    # The manifest file path.
    manifestPath: Path
    # The base root under which this pack was discovered
    # (one of the content roots or a saves root)
    sourceRoot: Path
    # Full raw manifest JSON/JSON5 data
    rawJson: Mapping[str, Any]
    # Canonical author name (string), derived from author when possible
    authorName: str | None = None



def _isUnder(path: Path, root: Path) -> bool:
    """
    Returns True if `path` is the same as `root` or nested under it.
    """
    return path == root or path.is_relative_to(root)



def _metaToResolved(meta: PackMeta) -> ResolvedPack:
    return ResolvedPack(
        id=meta.id,
        name=meta.name,
        kind=meta.kind,
        version=meta.version,
        rootDir=meta.packRoot,
        manifestPath=meta.manifestPath,
        sourceRoot=meta.baseRoot,
        rawJson=meta.rawJson,
        authorName=meta.authorName,
    )



class PackResolver:
    """
    High-level resolver built on top of PackMetaRegistry.
    
    Responsibilities:
      - Expose a ResolvedPack view for existing callers.
      - Provide SemVer-aware resolution for (kind, id[, authorName], versionReq?)
      - Support scoping resolution to one or more directory trees.
    """
    
    def __init__(self, registry: PackMetaRegistry | None = None) -> None:
        self._registry = registry
        
    def _getRegistry(self) -> PackMetaRegistry:
        if self._registry is None:
            self._registry = buildPackMetaRegistry()
        return self._registry
    
    # ----- Listing -----
    
    def listPacks(
        self,
        *,
        kinds: set[str] | None = None,
        roots: list[Path] | None = None,
    ) -> list[ResolvedPack]:
        """
        List packs of given kinds, optionally restricted to one or more directory trees.
        
        - If kinds is None, all pack kinds are returned.
        - If roots is None, results are taken from the full registry.
        - If roots is provided, only packs whose packRoot lies at or under
          any of the given roots are returned
        """
        registry = self._getRegistry()
        metas = registry.all()
        
        scopeRoots: list[Path] | None = None
        if roots is not None:
            norm: list[Path] = []
            seen: set[str] = set()
            for base in roots:
                resolved = str(Path(base).expanduser().resolve(strict=False))
                if resolved in seen:
                    continue
                seen.add(resolved)
                norm.append(Path(resolved))
            scopeRoots = norm
        
        out: list[ResolvedPack] = []
        for meta in metas:
            if kinds is not None and meta.kind not in kinds:
                continue
            if scopeRoots is not None and not any(_isUnder(meta.packRoot, root) for root in scopeRoots):
                continue
            out.append(_metaToResolved(meta))
        
        # Deterministic but simple ordering.
        out.sort(
            key=lambda pack: (
                pack.kind,
                str(pack.sourceRoot),
                str(pack.rootDir),
                pack.id,
                pack.version or "",
            )
        )
        return out

    
    # ----- Resolution -----

    def _candidateMetas(
        self,
        kind: str,
        packId: str,
        *,
        authorName: str | None,
        roots: list[Path] | None,
        preferSaves: bool,
    ) -> list[PackMeta]:
        registry = self._getRegistry()
        metas = registry.all()
        
        scopeRoots: list[Path] | None = None
        if roots is not None:
            norm: list[Path] = []
            seen: set[str] = set()
            for base in roots:
                resolved = str(Path(base).expanduser().resolve(strict=False))
                if resolved in seen:
                    continue
                seen.add(resolved)
                norm.append(Path(resolved))
            scopeRoots = norm
        
        candidates: list[PackMeta] = []
        for meta in metas:
            if meta.kind != kind:
                continue
            if meta.id != packId:
                continue
            if authorName is not None and meta.authorName != authorName:
                continue
            if scopeRoots is not None and not any(_isUnder(meta.packRoot, root) for root in scopeRoots):
                continue
            candidates.append(meta)
        
        # Save-layer packs should win ties over content-layer packs.
        # Within a layer, sort by baseRoot and packRoot for determinism.
        if preferSaves:
            candidates.sort(
                key=lambda mm: (
                    mm.rootLayer != "saves",
                    str(mm.baseRoot),
                    str(mm.packRoot),
                )
            )
        else:
            candidates.sort(
                key=lambda mm: (
                    str(mm.baseRoot),
                    str(mm.packRoot),
                )
            )
        return candidates
    
    def resolvePack(
        self,
        kind: str,
        packId: str,
        *,
        authorName: str | None = None,
        versionReq: str | SemVerPackRequirement | None = None,
        roots: list[Path] | None = None,
        preferSaves: bool = True,
    ) -> ResolvedPack | None:
        """
        Resolve a pack of a given kind using SemVer requirements.
        
        Arguments:
          - kind:       Pack kind ("appPack", "viewPack", "contentPack", "mod", "savePack", ...)
          - packId:     Logical pack id (e.g. "ai-chat")
          - authorName: Optional author filter; if set, only packs with matching canonical authorName are considered
          - versionReq: Semantic version requirement (string or SemVerPackRequirement),
                        For example:
                           "1.2.3"
                           ">=1.0.0 <2.0.0"
                           "^2.1.0"
                           "~1.4.0"
                        None means "any version".
          - roots:      Optional list of directory trees to restrict discovery.
                        Only packs whose packRoot lies at or under one of these
                        roots are considered.
          - preferSaves: when True (default), packs discovered from the saves
                        layer win ties over content-layer packs for the same
                        semantic version.
        
        SemVer behavior:
          - Only metas with a parsed semantic version participate in SemVer selection
          - If versionReq is not None and no SemVer-capable candidates exist,
            resolution fails (returns None)
          - If versionReq is None and no SemVer-capable candidates exist,
            resolution falls back to the first candidate after layering / ordering.
        """
        print("    >>>> resolvePack ", "kind", kind, "packId", packId, "authorName", authorName, "roots", roots)
        tracer = getTracer()
        span = None
        try:
            span = tracer.startSpan(
                "packs.resolve",
                attrs={
                    "kind": kind,
                    "packId": packId,
                    "authorName": authorName or "",
                    "hasVersionReq": bool(versionReq),
                },
                tags=["packs"],
            )
        except Exception:
            span = None
        
        try:
            candidates: list[PackMeta] = self._candidateMetas(
                kind,
                packId,
                authorName=authorName,
                roots=roots,
                preferSaves=preferSaves,
            )
            if not candidates:
                if span is not None:
                    tracer.endSpan(
                        span,
                        status="ok",
                        tags=["packs"],
                        attrs={"result": "none", "candidateCount": 0}
                    )
                return None
            
            # Normalize requirement.
            if isinstance(versionReq, str):
                requirement: SemVerPackRequirement | None = parseSemVerPackRequirement(versionReq)
            else:
                requirement = versionReq
            
            semverCandidates: list[tuple[SemVerPackVersion, PackMeta]] = [
                (meta.semver, meta)
                for meta in candidates
                if meta.semver is not None
            ]
            
            if requirement is not None and not semverCandidates:
                if span is not None:
                    tracer.endSpan(
                        span,
                        status="ok",
                        tags=["packs"],
                        attrs={
                            "result": "none-no-semver",
                            "candidateCount": len(candidates),
                        },
                    )
                return None
            
            # No SemVer data available at all → fall back to first candidate.
            if requirement is None and not semverCandidates:
                bestMeta = candidates[0]
                if span is not None:
                    tracer.endSpan(
                        span,
                        status="ok",
                        tags=["packs"],
                        attrs={
                            "result": "fallback",
                            "candidateCount": len(candidates),
                            "chosenId": bestMeta.id,
                            "chosenVersion": bestMeta.version or "",
                            "chosenLayer": bestMeta.rootLayer,
                        },
                    )
                return _metaToResolved(bestMeta)
            
            # Prefer saves-layer candidates in SemVer tie-breaks by ordering
            # the input to SemVerResolver accordingly. SemVerResolver chooses
            # the highest version, and for equal versions it keeps the first
            # encountered candidate.
            if preferSaves:
                semverCandidates.sort(
                    key=lambda item: (
                        item[1].rootLayer != "saves",
                        str(item[1].baseRoot),
                        str(item[1].packRoot),
                    )
                )
            
            matchResult = SemVerResolver.matchCandidates(semverCandidates, requirement)
            if matchResult.best is None:
                if span is not None:
                    tracer.endSpan(
                        span,
                        status="ok",
                        tags=["packs"],
                        attrs={
                            "result": "none-no-match",
                            "candidateCount": len(candidates),
                        },
                    )
                return None
            
            _bestVersion, bestMeta = matchResult.best
            if span is not None:
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["packs"],
                    attrs={
                        "result": "ok",
                        "candidateCount": len(candidates),
                        "chosenId": bestMeta.id,
                        "chosenVersion": bestMeta.version or "",
                        "chosenLayer": bestMeta.rootLayer,
                    },
                )
            return _metaToResolved(bestMeta)
        
        except Exception as exc:
            if span is not None:
                try:
                    tracer.endSpan(
                        span,
                        status="error",
                        tags=["packs", "error"],
                        errorType=type(exc).__name__,
                        errorMessage=str(exc),
                    )
                except Exception:
                    pass
            raise
    
    # ----- Convenience helpers -----
    
    def resolveAppPack(
        self,
        packId: str,
        *,
        authorName: str | None = None,
        versionReq: str | SemVerPackRequirement | None = None,
        roots: list[Path] | None = None,
    ) -> ResolvedPack | None:
        """
        Resolve an appPack by (id, authorName?, versionReq?, roots?).
        """
        return self.resolvePack(
            "appPack",
            packId,
            authorName=authorName,
            versionReq=versionReq,
            roots=roots,
        )
    
    def resolveViewPack(
        self,
        packId: str,
        *,
        authorName: str | None = None,
        versionReq: str | SemVerPackRequirement | None = None,
        roots: list[Path] | None = None,
    ) -> ResolvedPack | None:
        """
        Resolve a viewPack by (id, authorName?, versionReq?, roots?).
        
        Typical usage:
          - Global: resolveViewPack("testView")
          - Inside an appPack: resolveViewPack("testView", roots=[appPack.rootDir])
        """
        return self.resolvePack(
            "viewPack",
            packId,
            authorName=authorName,
            versionReq=versionReq,
            roots=roots,
        )
    
    def resolveViewPackForApp(
        self,
        appPack: ResolvedPack,
        viewKind: str | None,
        *,
        authorName: str | None = None,
        versionReq: str | SemVerPackRequirement | None = None,
    ) -> ResolvedPack | None:
        """
        Resolve a viewPack for a given appPack + viewKind with these rules:
        
            - viewKind is normalized to a non-empty string, defaulting to "main".
            - AppPack can declare which viewPack it owns via viewPacks in its
              manifest (list | dict | string).
            - If viewKind == "main":
              • Only ever resolve from inside the appPack (roots=[appPack.rootDir]).
              • Never resolved from global roots, so nothing external can override it.
              • If not found locally, returns None (no viewPack → plain appPack mods).
            - If viewKind != "main":
              • If declared in viewPacks in appPack's manifest → resolve locally only
                (roots=[appPack.rootDir]). No global fallback.
              • If NOT declared in viewPacks in appPack's manifest → try global viewPacks
                (roots=None), which means any external viewPack with that id can be used.
        """
        viewKind = (viewKind or "main").strip() or "main"
        
        rawJson = appPack.rawJson or {}
        meta = rawJson.get("meta") if isinstance(rawJson, dict) else None
        
        declared: set[str] = set()
        if isinstance(meta, dict):
            metaViewPacks = meta.get("viewPacks")
            if isinstance(metaViewPacks, str):
                name = metaViewPacks.strip()
                if name:
                    declared.add(name)
            elif isinstance(metaViewPacks, list):
                for item in metaViewPacks:
                    name = str(item or "").strip()
                    if name:
                        declared.add(name)
            elif isinstance(metaViewPacks, dict):
                for key in metaViewPacks.keys():
                    name = str(key or "").strip()
                    if name:
                        declared.add(name)
        
        # Special handling for "main":
        #   - Do NOT search globally (no override of default main).
        #   - Only resolve from inside the appPack. If not found, return None.
        if viewKind == "main":
            local = self.resolveViewPack(
                viewKind,
                authorName=authorName,
                versionReq=versionReq,
                roots=[appPack.rootDir],
            )
            return local
        
        # Non-main viewKind:
        # If the appPack declares this viewPack, we treat it as owned
        # and resolve ONLY from inside the appPack.
        if viewKind in declared:
            return self.resolveViewPack(
                viewKind,
                authorName=authorName,
                versionReq=versionReq,
                roots=[appPack.rootDir],
            )
        
        # AppPack does not declare this viewKind → allow external/global viewPack.
        # This is the "check viewPacks out of the appPack" case.
        return self.resolveViewPack(
            viewKind,
            authorName=authorName,
            versionReq=versionReq,
            roots=None,
        )
