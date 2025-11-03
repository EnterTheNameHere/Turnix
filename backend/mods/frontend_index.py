# backend/mods/frontend_index.py
from __future__ import annotations

from urllib.parse import quote
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.core.hashing import sha256sumWithPath
from backend.core.paths import resolveSafe
from backend.mods.constants import JS_RUNTIMES
from backend.mods.discover import scanMods, rescanMods, scanModsForMount, rescanModsForMount

router = APIRouter()



def makeFrontendIndex(
    found: dict,
    *,
    base: str,
    mountId: str | None = None,
) -> dict:
    # Avoid trailing slash issues ("/x" and "/x/" behave the same)
    base = "/" + base.strip("/")
    manifests: list[dict] = []

    for _modId, (_root, moddir, manifest, _manFileName) in found.items():
        rt = next((manifest.runtimes[key] for key in JS_RUNTIMES if key in manifest.runtimes), None)
        if not rt or not rt.enabled:
            continue
        
        entryPath = moddir / rt.entry
        # Compose URL with caller-supplied base (agnostic)
        # Caller passes:
        #   base="/mods/load"            → /mods/load/{modId}/{entry}
        #   base=f"/mods/{mountId}/load" → /mods/{mountId}/load/{modId}/{entry}
        modIdQuoted = quote(manifest.id, safe='@._-~')
        entryRel = f"{base}/{modIdQuoted}/{quote(rt.entry, safe='/')}"

        item = {
            "id": manifest.id,
            "name": manifest.name,
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
    return {
        "modManifests": manifests,
        "meta": {
            "base": base,
            "mountId": mountId,
            "count": len(manifests),
            "errors": errors,
        },
    }



@router.get("/mods/index")
def listFrontendMods() -> dict:
    return makeFrontendIndex(scanMods(), base="/mods/load", mountId=None)



@router.get("/mods/{mountId}/index")
def listFrontendModsForMount(mountId: str) -> dict:
    return makeFrontendIndex(scanModsForMount(mountId), base=f"/mods/{mountId}/load",mountId=mountId)



@router.get("/mods/load/{modId}/{path:path}")
def serveModAsset(modId: str, path: str):
    """
    Default (unmounted) asset serving: /mods/load/{modId}/{entry-or-asset-path}
    """
    found = scanMods()
    if modId not in found:
        raise HTTPException(404, "Unknown mod")
    _root, moddir, manifest, fname = found[modId]
    safe = resolveSafe(moddir, path or "main.js")
    if not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Requested path doesn't exist or is not a file.")
    resp = FileResponse(safe)
    # TODO: Make it configurable
    resp.headers["Cache-Control"] = "no-store"
    return resp



@router.get("/mods/{mountId}/load/{modId}/{path:path}")
def serveModAssetForMount(mountId: str, modId: str, path: str):
    """
    Mounted asset serving: /mods/{mountId}/load/{modId}/{entry-or-asset-path}
    """
    found = scanModsForMount(mountId)
    if not found:
        raise HTTPException(404, "Unknown mount")
    if modId not in found:
        raise HTTPException(404, "Unknown mod for mount")
    _root, moddir, _manifest, _fname = found[modId]
    safe = resolveSafe(moddir, path or "main.js")
    if not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Requested path does not exist or is not a file.")
    resp = FileResponse(safe)
    # TODO: Make it configurable
    resp.headers["Cache-Control"] = "no-store"
    return resp



@router.get("/mod/rescan")
def modsRescanMods():
    fresh = rescanMods()
    index = makeFrontendIndex(fresh, base="/mods/load", mountId=None)
    index["ok"] = True
    return index



@router.get("/mod/{mountId}/rescan")
def modsRescanMount(mountId: str):
    fresh = rescanModsForMount(mountId)
    index = makeFrontendIndex(fresh, base=f"/mods/{mountId}/load", mountId=mountId)
    index["ok"] = True
    return index
