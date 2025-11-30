# backend/content/runtime_bootstrap.py
from __future__ import annotations
import importlib.util
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from backend.app.globals import getRootsService
from backend.content.packs import PackResolver, ResolvedPack
from backend.content.saves import SaveManager
from backend.runtimes.instance import RuntimeInstance
from backend.runtimes.persistence import loadRuntime

logger = logging.getLogger(__name__)



GeneratorFn = Callable[[dict[str, Any]], dict[str, Any] | None]



@dataclass(frozen=True)
class ViewContext:
    """
    Bundles everything needed to bootstrap a view:
        - runtime instance (already ensured / generated)
        - appPack + viewPack (if any)
        - normalized viewKind
        - allowedMods as derived from the appPack manifest
        - extraModRoots: roots that should be passed into mod discovery for this view
    """
    runtimeInstance: RuntimeInstance
    appPack: ResolvedPack
    viewPack: ResolvedPack | None
    viewKind: str
    allowedMods: set[str]
    extraModRoots: tuple[Path, ...]



def _canonicalAppPackId(appPack: ResolvedPack) -> str:
    author = appPack.author or "unknown"
    return f"{author}@{appPack.id}"



def _extractMods(appPack: ResolvedPack) -> set[str]:
    rawJson = appPack.rawJson or {}
    rawMeta = rawJson.get("meta")
    mods = rawMeta.get("mods") if isinstance(rawMeta, dict) else None
    
    if mods is None:
        return set()
    
    # dict → keys are mod names
    if isinstance(mods, dict):
        return {str(key).strip() for key in mods.keys() if str(key).strip()}
    
    # Reject strings early - they are iterable!
    if isinstance(mods, str):
        raise TypeError(f"Invalid 'mods' value type - use dict or list!")
    
    # list of mod names
    if isinstance(mods, list):
        return {str(mod).strip() for mod in mods if str(mod).strip()}

    raise TypeError(f"Invalid 'mods' value type: {type(mods)}. Use dict or list!")



def _defaultInstanceId(appPack: ResolvedPack) -> str:
    rawMeta = appPack.rawJson.get("meta") if isinstance(appPack.rawJson, dict) else None
    runtimeCfg = rawMeta.get("runtimes") if isinstance(rawMeta, dict) else {}
    if isinstance(runtimeCfg, dict) and "defaultInstanceId" in runtimeCfg:
        value = runtimeCfg.get("defaultInstanceId")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{appPack.id}-runtime"



def _loadGeneratorModule(generatorPath: Path):
    spec = importlib.util.spec_from_file_location(generatorPath.stem, generatorPath)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load generator from {generatorPath}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def _runGenerator(generatorPath: Path, context: dict[str, Any]) -> dict[str, Any]:
    module = _loadGeneratorModule(generatorPath)
    generate = getattr(module, "generate", None)
    if not callable(generate):
        raise RuntimeError(f"Generator at '{generatorPath}' does not expose generate()")
    
    generatorFn = cast(GeneratorFn, generate)
    result = generatorFn(context)
    if result is None:
        return {}
    
    if not isinstance(result, dict):
        raise TypeError(
            f"Generator at '{generatorPath}' must return dict[str, Any] or None, got {type(result).__name__}"
        )
    
    return result



def _discoverSaveDir(baseCandidates: list[Path], appPackId: str, runtimeInstanceId: str | None) -> Path | None:
    for base in baseCandidates:
        appPackSaveRoot = Path(base) / appPackId
        specific = appPackSaveRoot / (runtimeInstanceId or "") if runtimeInstanceId else None
        if specific and (specific / "save.json5").exists():
            return specific
        if not appPackSaveRoot.exists() or not appPackSaveRoot.is_dir():
            continue
        for child in sorted(appPackSaveRoot.iterdir()):
            if not child.is_dir():
                continue
            if (child / "save.json5").exists():
                return child
    return None



def _generateRuntime(
    *,
    appPack: ResolvedPack,
    appKey: str,
    runtimeInstanceId: str,
    baseDir: Path,
) -> Path:
    rawMeta = appPack.rawJson.get("meta") if isinstance(appPack.rawJson, dict) else {}
    runtimesCfg = rawMeta.get("runtimes") if isinstance(rawMeta, dict) else None
    generatorRel = "generator.py"
    if isinstance(runtimesCfg, dict):
        gen = runtimesCfg.get("generator")
        if isinstance(gen, str) and gen.strip():
            generatorRel = gen.strip()
    generatorPath = (appPack.rootDir / generatorRel).resolve()
    if not generatorPath.exists() or not generatorPath.is_file():
        raise RuntimeError(f"Generator file '{generatorPath}' for appPack '{appPack.id}' does not exist")
    
    targetDir = (baseDir / appKey / runtimeInstanceId).resolve()
    targetDir.mkdir(parents=True, exist_ok=True)
    ctx = {
        "appPackId": appKey,
        "runtimeInstanceId": runtimeInstanceId,
        "saveDir": str(targetDir),
        "label": appPack.name or appPack.id,
    }
    
    result = _runGenerator(generatorPath, ctx)
    
    # Allow generator to override targetDir via "saveDir" in its result
    if isinstance(result, dict):
        saveDir = result.get("saveDir")
        if isinstance(saveDir, str) and saveDir.strip():
            targetDir = Path(saveDir.strip()).resolve()
    
    saveFile = targetDir / "save.json5"
    if not saveFile.exists():
        raise RuntimeError(f"Generator for appPack {appPack.id} did not create '{saveFile}'")
    
    logger.info("Generated savePack for '%s' at '%s'", appKey, str(targetDir))
    return targetDir



def ensureRuntimeForAppPack(
    appPackIdOrQId: str,
    *,
    preferEmbeddedSaves: bool = False,
) -> tuple[RuntimeInstance, ResolvedPack]:
    resolver = PackResolver()
    # Perform discovery so all packs are visible before save loading/generation...
    resolver.listPacks()
    appPack = resolver.resolveAppPack(appPackIdOrQId)
    if not appPack:
        raise RuntimeError(f"AppPack '{appPackIdOrQId}' not found. Cannot create RuntimeInstance.")
    
    canonicalId = _canonicalAppPackId(appPack)
    runtimeInstanceId = _defaultInstanceId(appPack)
    
    baseCandidates: list[Path] = []
    if preferEmbeddedSaves:
        baseCandidates.append(appPack.rootDir / "saves")
    baseCandidates.append(getRootsService().getWriteDir("saves"))
    
    appKey = SaveManager().appIdToKey(canonicalId)
    saveDir = _discoverSaveDir(baseCandidates, appKey, runtimeInstanceId)
    
    if saveDir is None:
        primaryBase = baseCandidates[0]
        saveDir = _generateRuntime(
            appPack=appPack,
            appKey=appKey,
            runtimeInstanceId=runtimeInstanceId,
            baseDir=primaryBase,
        )
    
    runtimeInstance = loadRuntime(saveDir)
    runtimeInstance.setAllowedPacks(_extractMods(appPack))
    
    return runtimeInstance, appPack



def ensureViewContext(
    appPackIdOrQId: str,
    viewKind: str | None = None,
    *,
    preferEmbeddedSaves: bool = False,
) -> ViewContext:
    """
    High-level helper that:
      1) Ensures a RuntimeInstance + appPack + allowedMods exist.
      2) Normalizes viewKind (defaults to "main").
      3) Resolves the viewPack for the given appPack + viewKind, with the
         rules implemented by PackResolver.resolveViewPackForApp()
      4) Computes extraModRoots for this view (currently: [viewPack.rootDir]
         when a viewPack is resolved, otherwise empty).
    """
    # Step 1: runtime + appPack + allowedMods
    runtimeInstance, appPack = ensureRuntimeForAppPack(
        appPackIdOrQId,
        preferEmbeddedSaves=preferEmbeddedSaves,
    )
    
    # Step 2: normalize viewKind
    normalizedViewKind = (viewKind or "main").strip() or "main"
    
    # Step 3: resolve viewPack within appPack scope
    resolver = PackResolver()
    viewPack = resolver.resolveViewPackForApp(appPack, normalizedViewKind)
    
    # Step 4: extra mod roots for this view (for use with loadPythonMods → extraRoots)
    extraRoots: list[Path] = []
    if viewPack is not None:
        extraRoots.append(viewPack.rootDir)
    
    return ViewContext(
        runtimeInstance=runtimeInstance,
        appPack=appPack,
        viewPack=viewPack,
        viewKind=normalizedViewKind,
        allowedMods=runtimeInstance.allowedPacks,
        extraModRoots=tuple(extraRoots),
    )
