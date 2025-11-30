# backend/app/factory.py
from __future__ import annotations
import logging
from collections.abc import Sequence

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.context import PROCESS_REGISTRY
from backend.app.globals import getTracer
from backend.app.lifecycle import life
from backend.app.static_mount import mountStatic
from backend.content.runtime_bootstrap import ensureRuntimeForAppPack
from backend.kernel import Kernel
from backend.rpc.transport import mountWebSocket
from backend.runtimes.instance import RuntimeInstance



def createApp(*, extraRouters: Sequence[APIRouter] = (), initialRuntime: RuntimeInstance | None = None) -> FastAPI:
    # Early, process-wide bootstrap (idempotent)
    tracer = getTracer()
    tracer.startProcessSpan({"phase": "factory.createApp"})
    from backend.content.roots import initRoots
    initRoots() # TODO: Add cli options
    from backend.app.config import initConfig
    initConfig()
    from backend.core.logger import configureLogging
    configureLogging()
    from backend.core.permissions import initPermissions
    initPermissions()
    
    logger = logging.getLogger(__name__)
    
    # Find how many unresolved references we have in config schemas
    from backend.app.globals import getConfigService
    unresolved = getConfigService().registry.findUnresolvedRefs()
    if unresolved:
        logger.warning("Config: unresolved $ref ids: %s", ", ".join(unresolved))
    
    # Turnix Boss. It registers itself to globals.
    kernel = Kernel()
    
    # Create or load main menu runtime
    activeAppPack = None
    if initialRuntime is None:
        runtimeInstance, appPack = ensureRuntimeForAppPack(
            "Turnix@main-menu",
            preferEmbeddedSaves=True
        )
        activeAppPack = appPack
    else:
        runtimeInstance = initialRuntime
    
    kernel.switchRuntime(runtimeInstance)
    if activeAppPack is not None:
        PROCESS_REGISTRY.register("runtime.active.appPack", activeAppPack, overwrite=True)
    
    app = FastAPI(lifespan=life)
    
    from backend.app.globals import config
    # ----- CORS (cookies-ready) -----
    corsOrigins = config("http.cors.allowOrigins", ["http://localhost:5173", "http://127.0.0.1:5173"])
    if not isinstance(corsOrigins, list):
        corsOrigins = []

    app.add_middleware(
        CORSMiddleware,
        allow_origins=corsOrigins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ----- Routers first -----
    from backend.api.bootstrap import router as bootstrapRouter
    from backend.app.trace_ws import router as traceRouter
    from backend.app.web import router as webRouter
    from backend.mods.frontend_index import router as frontendRouter

    app.include_router(bootstrapRouter)
    app.include_router(traceRouter)
    app.include_router(webRouter)
    app.include_router(frontendRouter)

    for router in extraRouters:
        app.include_router(router)

    # ----- WebSocket endpoint -----
    mountWebSocket(app)

    # ----- Static comes last so it doesn't shadow routes -----
    mountStatic(app)

    logger.info("Backend initialized with %d extra routers(s)", len(extraRouters))
    return app
