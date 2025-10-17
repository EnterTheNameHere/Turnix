# backend/app/static_mount.py
from __future__ import annotations
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.app.paths import WEBROOT

logger = logging.getLogger(__name__)



def mountStatic(app: FastAPI) -> None:
    if WEBROOT.exists():
        # Mount "/" LAST so it doesn't shadow other routes!
        app.mount("/", StaticFiles(directory=WEBROOT, html=True), name="web")
        logger.info("Static mounted at / -> %s", WEBROOT)
    else:
        logger.warning("WEBROOT '%s' not found; static hosting disabled.", WEBROOT)

