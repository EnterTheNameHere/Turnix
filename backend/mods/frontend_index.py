# backend/mods/frontend_index.py
from __future__ import annotations

import urllib.parse
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.app.globals import getActiveAppPack, getActiveRuntime, getTracer
from backend.core.hashing import sha256sumWithPath
from backend.core.paths import resolveSafe
from backend.mods.constants import JS_RUNTIMES
from backend.mods.discover import scanMods

router = APIRouter()



def makeFrontendIndex(
    found: dict,
    *,
    viewId: str,
) -> dict:
    manifests: list[dict] = []

    for _modId, (_root, moddir, manifest, _manFileName) in found.items():
        rt = next((manifest.runtimes[key] for key in JS_RUNTIMES if key in manifest.runtimes), None)
        if not rt or not rt.enabled:
            continue
        
        entryPath = moddir / rt.entry
        modIdQuoted = urllib.parse.quote(manifest.id, safe='@._-~')
        entryRel = f"views/{viewId}/mods/load/{modIdQuoted}/{urllib.parse.quote(rt.entry, safe='/')}"

        item = {
            "id": manifest.id,
            "displayName": manifest.displayName,
            "version": manifest.version,
            "entry": entryRel,
            "runtime": "javascript",
            "order": rt.order,
            "permissions": rt.permissions,
            "capabilities": rt.capabilities,
            "hidden": manifest.hidden,
        }

        if entryPath.exists():
            fileHash = sha256sumWithPath(entryPath)
            item["hash"] = fileHash
            item["enabled"] = True
            # Cache-bust: append ?v=<hash> so reloads see changes
            item["entry"] = f"{entryRel}?v={fileHash}"
        else:
            item["enabled"] = False
            item["problems"] = [{
                "id": manifest.id,
                "runtime": "javascript",
                "entry": str(entryPath),
                "reason": "Entry file not found.",
                "stack": "",
            }]
        
        # Ensure entry url set even when disabled
        item.setdefault("entry", entryRel)
        manifests.append(item)
    
    manifests.sort(key=lambda man: (man.get("order", 0), man["id"], man["version"]))
    errors = sum(1 for manifest in manifests if not manifest.get("enabled"))
    
    try:
        tracer = getTracer()
        tracer.traceEvent(
            "mods.frontend.indexBuild",
            level="debug",
            tags=["mods", "frontend"],
            attrs={
                "count": len(manifests),
                "errors": errors,
                "ids": [man["id"] for man in manifests],
            },
        )
    except Exception:
        pass
    
    return {
        "modManifests": manifests,
        "meta": {
            "count": len(manifests),
            "errors": errors,
        },
    }



@router.get("/views/{viewId}/mods/index")
def listFrontendModsForView(viewId: str) -> dict:
    tracer = getTracer()
    try:
        tracer.traceEvent(
            "mods.frontend.listFrontendModsForView",
            level="info",
            tags=["mods", "frontend"],
            attrs={"viewId": viewId},
        )
    except Exception:
        pass
    
    runtimeInstance = getActiveRuntime()
    result = makeFrontendIndex(
        scanMods(
            allowedIds=runtimeInstance.getAllowedPacks(),
            appPack=getActiveAppPack(),
            saveRoot=runtimeInstance.saveRoot
        ),
        viewId=viewId,
    )
    return result



@router.get("/views/{viewId}/mods/load/{modId}/{path:path}")
def serveModAssetForView(viewId: str, modId: str, path: str) -> FileResponse:
    tracer = getTracer()
    try:
        tracer.traceEvent(
            "mods.frontend.serveModAssetForView",
            level="info",
            tags=["mods", "frontend"],
            attrs={"viewId": viewId},
        )
    except Exception:
        pass
    
    activeRuntime = getActiveRuntime()
    if activeRuntime is None:
        raise HTTPException(status_code=404, detail="Unknown View.")
    found = scanMods(
        allowedIds=activeRuntime.allowedPacks,
        appPack=getActiveAppPack(),
        saveRoot=getActiveRuntime().saveRoot or None,
    )
    if modId not in found:
        raise HTTPException(404, "Unknown mod.")
    _root, moddir, _manifest, _manifestFileName = found[modId]
    safe = resolveSafe(moddir, path)
    if not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Requested path doesn't exist or is not a file.")
    response = FileResponse(safe)
    # TODO: Make it configurable
    response.headers["Cache-Control"] = "no-store"
    return response



@router.get("/views/{viewId}/mods/rescan")
def rescanModsForView(viewId: str) -> dict:
    tracer = getTracer()
    try:
        tracer.traceEvent(
            "mods.frontend.rescanModsForView",
            level="info",
            tags=["mods", "frontend"],
            attrs={"viewId": viewId},
        )
    except Exception:
        pass
    
    return listFrontendModsForView(viewId)
