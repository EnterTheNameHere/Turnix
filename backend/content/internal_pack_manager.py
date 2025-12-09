# backend/content/internal_pack_manager.py
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from backend.content.pack_descriptor import (
    PackDescriptor,
    PackDescriptorRegistry,
    PackKind,
    PackRequest,
    buildPackDescriptorRegistry,
)
from backend.content.pack_resolution import (
    parsePackRefString,
    resolvePackSelector,
    PackResolutionError,
)
from backend.semver.semver import SemVerPackRequirement, parseSemVerPackRequirement

logger = logging.getLogger(__name__)

__all__ = [
    "ActivationEntry",
    "PackActivationPlan",
    "InternalPackManager",
]



# ------------------------------------------------------------------ #
# Data structures
# ------------------------------------------------------------------ #

@dataclass(slots=True, frozen=True)
class ActivationEntry:
    """
    A single step in the activation plan.
    
    - descriptor: The pack to be prepared / activated.
    - reason: Why this pack is in the plan ("root", "dependency", "recommended").
    - required: If True, absence or failure is fatal for the plan.
    - depth: Graph depth from the root (0 == root)
    """
    descriptor: PackDescriptor
    reason: str
    required: bool
    depth: int



@dataclass(slots=True, frozen=True)
class PackActivationPlan:
    """
    Final, engine-internal representation of which packs should be activated
    and in what order.
    
    This plan does not perform any loading itself; higher-level systems
    (config loader, runtimes, mod loader) consume this plan.
    
    - roots: The root packs this plan was built for (for example, one appPack
      and zero/one viewPack).
    - entries: Ordered activation steps. Higher layers run packs in this order.
    """
    roots: tuple[PackDescriptor, ...]
    entries: tuple[ActivationEntry, ...]



# ------------------------------------------------------------------ #
# InternalPackManager
# ------------------------------------------------------------------ #

class InternalPackManager:
    """
    Engine-internal pack planner.
    
    Responsibilities:
        - Given one or more root PackDescriptors (typically an appPack and/or
          a viewPack), compute the dependency closure.
        - Resolve  dependency selectors to concrete PackDescriptors using the
          PackDescriptorRegistry and resolvePackSelector.
        - Detect dependency cycles and report them clearly.
        - Produce a PackActivationPlan which higher-level code can use to drive:
            • schema/config loading
            • runtime entry loading
            • mod onLoad invocation
            • later lifecycle hooks
    
    This manager is intentionally not exposed to mods. It is part of the
    engine core and is responsible for deterministic, safe resolution.
    """
    _registry: PackDescriptorRegistry
    
    def __init__(self, registry: PackDescriptorRegistry) -> None:
        self._registry = registry
    
    @classmethod
    def fromDiscovery(cls) -> InternalPackManager:
        """
        Build a new manager using full discovery across content + saves roots.
        """
        reg = buildPackDescriptorRegistry()
        return cls(registry=reg)
    
    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    
    def buildPlanForApp(
        self,
        app: PackDescriptor,
        *,
        extraRoots: Iterable[PackDescriptor] | None = None,
    ) -> PackActivationPlan:
        """
        Build an activation plan starting from an appPack.
        
        extraRoots can contain additional root packs (for example, a
        viewPack that should be activated alongside the app).
        """
        roots: list[PackDescriptor] = [app]
        if extraRoots is not None:
            roots.extend(extraRoots)
        return self._buildPlan(roots)
    
    def buildPlanForRoots(
        self,
        roots: Iterable[PackDescriptor],
    ) -> PackActivationPlan:
        """
        Build an activation plan for an arbitrary set of root packs
        (for example, appPack + viewPack).
        """
        rootList = list(roots)
        if not rootList:
            raise ValueError("InternalPackManager.buildPlanForRoots: roots cannot be empty")
        return self._buildPlan(rootList)
    
    # ------------------------------------------------------------------ #
    # Core planning
    # ------------------------------------------------------------------ #
    
    def _buildPlan(
        self,
        roots: list[PackDescriptor],
    ) -> PackActivationPlan:
        """
        Compute dependency closure and calculate a stable activation order.
        
        Current ordering rules:
            - Depth-first traversal from each root.
            - A pack is added on first encounter; repeated references reuse
              the first ActivationEntry.
            - Depth is recorded from the first encounter.
            - Within the same depth, the order is the order of discovery.
        """
        # Key: (kind, effectiveAuthor, packTreeId)
        visited: dict[tuple[PackKind, str, str], ActivationEntry] = {}
        order: list[ActivationEntry] = []
        
        def visit(
            desc: PackDescriptor,
            *,
            reason: str,
            required: bool,
            depth: int,
            stack: list[tuple[PackKind, str, str]],
        ) -> None:
            key = (desc.kind, desc.effectiveAuthor, desc.packTreeId)
            
            if key in stack:
                cycle = " -> ".join(
                    f"{kk[0].value}:{kk[1]}@{kk[2]}" for kk in (*stack, key)
                )
                msg = f"Detected pack dependency cycle: {cycle}"
                # This is an engine error and should be fatal.
                logger.error(msg)
                raise RuntimeError(msg)
            
            if key in visited:
                # Already planned, nothing more to do. We do not update depth/reason.
                return
            
            entry = ActivationEntry(
                descriptor=desc,
                reason=reason,
                required=required,
                depth=depth,
            )
            visited[key] = entry
            order.append(entry)
            
            # Walk hard dependencies first.
            newStack = [*stack, key]
            for depReq in self._extractHardDependencies(desc):
                try:
                    depDesc = resolvePackSelector(
                        self._registry,
                        depReq,
                        kind=depReq.kind,
                        requestingPack=desc,
                    )
                except PackResolutionError as err:
                    if required:
                        raise
                    logger.warning(
                        "Non-fatal dependency resolution failure for %s: %s",
                        desc.packTreeId,
                        err,
                    )
                    continue
                
                visit(
                    depDesc,
                    reason="dependency",
                    required=True,
                    depth=depth + 1,
                    stack=newStack,
                )
            
            # Optional/recommended packs - they are part of the plan but
            # resolution failures are non-fatal.
            for optReq in self._extractOptionalDependencies(desc):
                try:
                    optDesc = resolvePackSelector(
                        self._registry,
                        optReq,
                        kind=optReq.kind,
                        requestingPack=desc,
                    )
                except PackResolutionError as err:
                    logger.info(
                        "Optional dependency not resolved for %s: %s",
                        desc.packTreeId,
                        err,
                    )
                    continue
                
                visit(
                    optDesc,
                    reason="recommended",
                    required=False,
                    depth=depth + 1,
                    stack=newStack,
                )
        
        # Run DFS from each root.
        for root in roots:
            visit(
                root,
                reason="root",
                required=True,
                depth=0,
                stack=[],
            )
        
        # At this stage `order` is already in depth-first order with
        # deterministic traversal. TODO: When we introduce category-based and
        # priority-based ordering, this is where to refine the sort.
        entries = tuple(order)
        return PackActivationPlan(
            roots=tuple(roots),
            entries=entries,
        )
    
    # ------------------------------------------------------------------ #
    # Dependency extraction
    # ------------------------------------------------------------------ #
    
    def _extractHardDependencies(self, desc: PackDescriptor) -> list[PackRequest]:
        """
        Read manifest-level *required* dependencies for a pack.
        
        This is the engine's view on "things that must be present":
          - Required mods for an appPack or viewPack.
          - Required dependencies for a mod (for example, runtime support).
        
        Current conservative implementation:
          - For appPack/viewPack:
              • reads manifest["mods"] if present, treating it as:
                { "<packTreeId>": "<semver requirement or empty" }
          - For other kinds:
              • returns an empty list for now.
        
        This function should evolve alongside pack-manifest-structure.txt
        (for example, to also read "requires", "contentPacks", etc.)
        """
        raw = desc.rawJson
        
        # Only appPack and viewPack have well-known "mods" right now.
        if desc.kind not in (PackKind.APP, PackKind.VIEW):
            return []

        modsVal = raw.get("mods")
        if not isinstance(modsVal, dict):
            return []
        
        hardDeps: list[PackRequest] = []
        
        for key, val in modsVal.items():
            if not isinstance(key, str):
                continue
            packTreeId = key.strip()
            if not packTreeId:
                continue
            
            # Value may be a pure semver range ["^1.0.0"] or a full PackRefString.
            if isinstance(val, str) and val.strip():
                text = val.strip()
                if "@" in text or "://" in text:
                    # Looks like a full selector - let parsePackRefString handle it
                    try:
                        req = parsePackRefString(text, kind=PackKind.MOD)
                        hardDeps.append(req)
                        continue
                    except Exception as err:
                        logger.warning(
                            "Invalid PackRefString '%s' in mods of %s, %s",
                            text,
                            desc.packTreeId,
                            err,
                        )
                        # Fall through to treating it as semver-only if possible.
                    
                # Treat as semver requirement for the key id.
                try:
                    semverReq: SemVerPackRequirement | None = parseSemVerPackRequirement(text)
                except Exception as err:
                    logger.warning(
                        "Invalid SemVer requirement '%s' for mod '%s' in %s: %s",
                        text,
                        packTreeId,
                        desc.packTreeId,
                        err,
                    )
                    semverReq = None
                
                hardDeps.append(
                    PackRequest(
                        author=None,
                        packTreeId=packTreeId,
                        semverRequirement=semverReq,
                        kind=PackKind.MOD,
                    )
                )
            else:
                # No value or non-string: treat as "any version of <key>".
                hardDeps.append(
                    PackRequest(
                        author=None,
                        packTreeId=packTreeId,
                        semverRequirement=None,
                        kind=PackKind.MOD,
                    )
                )
        
        return hardDeps

    def _extractOptionalDependencies(self, desc: PackDescriptor) -> list[PackRequest]:
        """
        Read manifest-level *optional* or "recommended" dependencies.
        
        At the moment this is a stub that simply returns desc.recommendedPacks,
        which are already normalized PackRequest instances.
        """
        if not desc.recommendedPacks:
            return []
        return list(desc.recommendedPacks)

    def getRegistry(self) -> PackDescriptorRegistry:
        return self._registry
    