# backend/app/web.py
from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.app.config import getGlobalConfig

router = APIRouter()



@router.get("/settings")
async def getSettings():
    return JSONResponse(getGlobalConfig().snapshot(), status_code=200)



@router.get("/health")
async def health():
    # TODO: add llama.cpp or other driver health here too
    return {"ok": True, "ts": int(time.time() * 1000)}
