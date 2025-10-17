# backend/mods/frontend_index.py
from __future__ import annotations

from urllib.parse import quote
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.core.hashing import sha256sumWithPath
from backend.core.paths import resolveSafe
from backend.mods.constants import JS_RUNTIMES
from backend.mods.discover import scanMods, rescanMods

router = APIRouter()



def makeFrontendIndex() -> dict:
    found = scanMods()
    manifests: list[dict] = []

    for _modId, (_root, moddir, manifest, _manFileName) in found.items():
        rt = next((manifest.runtimes[key] for key in JS_RUNTIMES if key in manifest.runtimes), None)
        if not rt or not rt.enabled:
            continue
        
        entryPath = moddir / rt.entry
        entryURL = f"/mods/load/{manifest.id}/{quote(rt.entry, safe='/')}"

        item = {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "entry": entryURL,
            "runtime": "javascript",
            "order": rt.order,
            "permissions": rt.permissions,
            "capabilities": rt.capabilities,
            "hidden": manifest.hidden,
        }

        if entryPath.exists():
            item["hash"] = sha256sumWithPath(entryPath)
            item["enabled"] = True
        else:
            item["enabled"] = False
            item["problems"] = [{
                "id": manifest.id,
                "runtime": "javascript",
                "entry": str(entryPath),
                "reason": "Entry file not found.",
                "stack": "",
            }]
        
        manifests.append(item)
    
    manifests.sort(key=lambda man: (man.get("order", 0), man["id"], man["version"]))
    return {"modManifests": manifests}



@router.get("/mods/index")
def listFrontendMods() -> dict:
    return makeFrontendIndex()



@router.get("/mods/load/{modId}/{path:path}")
def serveModAsset(modId: str, path: str):
    found = scanMods()
    if modId not in found:
        raise HTTPException(404, "Unknown mod")
    
    _root, moddir, _manifest, _fname = found[modId]
    
    safe = resolveSafe(moddir, path or "main.js")
    
    if not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Requested path does not exist or is not a file.")
    
    # Def-friendly: no-cache. If we need perf, we could use ETag/Cache-Control.
    return FileResponse(safe)



@router.get("/mod/rescan")
def modsRescanMods():
    fresh = rescanMods()
    return {"ok": True, "count": len(fresh)}
