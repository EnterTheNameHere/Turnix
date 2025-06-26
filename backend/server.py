import logging

from core.logger import configureLogging
configureLogging()

# Disable propagation from common libraries
for name in [
    "uvicorn", "uvicorn.access", "uvicorn.error",
    "fastapi", "concurrent.futures", "asyncio",
    "httpcore.connection", "httpcore.http11",
    "httpx"
]:
    logging.getLogger(name).propagate = False

from core.logger import getJSLogHandler
getJSLogHandler().setReady()

from core.frontend_server import FrontendServer
frontendServer = FrontendServer(port=3000, directory="frontend")
frontendServer.start()

import atexit
atexit.register(frontendServer.stop)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.observer_bus import ObserverBus
from core.pipeline_stages import PipelineStage
from backend.rpc_websocket import websocketRpcEndpoint, registerRpc
from backend.rpc import FrontendRPC
from backend.pipeline_controller import PipelineController

import logging
logger = logging.getLogger(__name__)

import datetime
current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
logger.info(f"| {current_time} | Starting up backend...")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_websocket_route("/ws", websocketRpcEndpoint)

observerBus = ObserverBus()
pipelineEngine = PipelineController(observerBus)
frontendRPC = FrontendRPC()

observerBus.callFrontend = frontendRPC.callFrontendHook

sessions = {}

@app.get("/")
def root():
    return {"status": "dirigent running"}

@app.post("/run_pipeline")
async def runPipeline(payload: dict):
    logger.info(f"[/run_pipeline] Received payload: {payload}")
    sessionId = payload['data']['sessionId']
    userMessage = payload['data']['userMessage']
    result = await pipelineEngine.process(sessionId, userMessage)
    return result

@app.post("/register_frontend_hooks")
async def registerFrontendHooks(payload: dict):
    logger.info(f"[/register_frontend_hooks] Received payload: {payload}")
    for hook in payload["hooks"]:
        observerBus.register(
            stage=hook["stage"],
            name=hook["name"],
            handler=None,
            before=hook.get("before", []),
            after=hook.get("after", []),
            location="frontend",
        )
    return {"status": "registered"}

@app.post("/frontend_execute_hook")
async def frontendExecuteHook(payload: dict):
    logger.info(f"[/frontend_execute_hook] Received payload: {payload}")
    hookName = payload["name"]
    stage = payload["stage"]
    data = payload["data"]
    logger.info(f"Frontend executed hook '{hookName}' at stage '{stage}'.")
    return {"data": data}
