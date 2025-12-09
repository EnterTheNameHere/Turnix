# backend/app/static_mount.py
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.content.content_roots import WEB_ROOT

logger = logging.getLogger(__name__)



def mountStatic(app: FastAPI) -> None:
    if WEB_ROOT.exists():
        # Mount "/" LAST so it doesn't shadow other routes!
        app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
        logger.info("Static mounted at / -> %s", WEB_ROOT)
    else:
        logger.warning("WEB_ROOT '%s' not found; static hosting disabled.", WEB_ROOT)

