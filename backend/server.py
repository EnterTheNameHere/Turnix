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

from backend.turnix import Turnix as _Turnix
Turnix = _Turnix(frontendServer)

from backend import globals as GlobalState
GlobalState.Turnix = Turnix

import atexit
atexit.register(frontendServer.stop)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import logging
logger = logging.getLogger(__name__)

import time
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

class ModLoadingError(Exception):
    pass

@app.get("/")
def root():
    return {"status": "dirigent running"}

@app.post("/run_pipeline")
async def runPipeline(payload: dict):
    logger.info(f"[/run_pipeline] Received payload: {payload}")
    sessionId = payload['data']['sessionId']
    logger.info(f"[/run_pipeline] sessionId: {sessionId}")
    userMessage = payload['data']['userMessage']
    logger.info(f"[/run_pipeline] userMessage: {userMessage}")
    result = await Turnix.session.sendUserMessage(userMessage)
    #result = await pipelineEngine.process(sessionId, userMessage)
    return result

@app.post("/frontend_execute_hook")
async def frontendExecuteHook(payload: dict):
    logger.info(f"[/frontend_execute_hook] Received payload: {payload}")
    hookName = payload["name"]
    stage = payload["stage"]
    data = payload["data"]
    logger.info(f"Frontend executed hook '{hookName}' at stage '{stage}'.")
    return {"data": data}

from fastapi import WebSocket, WebSocketDisconnect
from backend.resilient_websocket import ResilientWebSocket
from backend.view import RPCError

class Server:
    def __init__(self):
        app.add_websocket_route("/viewws", self.viewwsRpcEndpoint)
        logger.info("Server initialized.")

    async def viewwsRpcEndpoint(self, websocket: WebSocket):
        clientId = 0
        view = None
        try:
            await websocket.accept()
            resilientWs = ResilientWebSocket(websocket)
            
            # 1. Receive the initial message from the frontend.
            firstMessage = await resilientWs.receive()
            logger.debug(f"[viewws] Received initial message from frontend. firstMessage: {firstMessage}")

            if firstMessage.get("type") != "frontendEmit" or firstMessage.get("action") != "identifyView":
                await resilientWs.close(code=4000, reason="Invalid request type")
                logger.debug("[viewws] Invalid request type, closing connection with code 4000.")
                return

            viewId = firstMessage.get('viewId')
            clientId = firstMessage.get('clientId')

            if not isinstance(viewId, str) or not isinstance(clientId, int):
                await resilientWs.close(code=4001, reason="Invalid viewId or clientId")
                logger.debug(f"[viewws: ({viewId},{clientId})] Invalid viewId or clientId, closing connection with code 4001.")
                return

            # 2. View should be created before receiving the initial message.
            view = Turnix.viewManager.getView(viewId, clientId)
            if view is None:
                await resilientWs.close(code=4002, reason="Main view not found")
                logger.error(f"[viewws: ({viewId},{clientId})] 💥 FATAL: Main view not registered in ViewManager. It should already exist by this time. This is unexpected error. Aborting connection.")
                return
            else:
                view = Turnix.viewManager.createView(viewId, clientId)

            # 3. Assign socket to view.
            if view.getSocket() is not None:
                logger.warning(f"[viewws: ({viewId},{clientId})] 💥 WARNING: Socket already assigned to this view. This is unexpected situation. Replacing socket.")
            view.assignSocket(resilientWs)

            # 4. Handle mods.
            # We are running temporary loop to synchronously load mods in predictable order.
            # It's possible that some mod might send message before loading is fully completed,
            # so we cache messages which are not part of loading process for later.
            cachedMessages = []
            if view.viewId == "main" and firstMessage["action"] == "identifyView":
                # 4.1. Scan for mods.
                await Turnix.modManager.scanForAllMods("mods")
                orderedManifestList = await Turnix.modManager.sortModManifestsOrder()
                
                # 4.2. Load mods.
                loadedMods = []
                for _, manifest in orderedManifestList:
                    requestId = f"loadJSMod_{manifest.path}"
                    await resilientWs.send({
                        "type": "backendRequest",
                        "action": "loadJSMod",
                        "data": manifest.model_dump(),
                        "clientId": view.clientId,
                        "viewId": view.viewId,
                        "securityToken": view.securityToken,
                        "timestamp": int(time.time() * 1000),
                        "requestId": requestId,
                    })

                    while True:
                        # TODO: Add timeout for awaiting loadJSMod action
                        replyMessage = await resilientWs.receive()
                        logger.debug(f"Reveived message while waiting for loadJSMod reply: {replyMessage}")

                        if replyMessage.get("type") == "frontendReply" and replyMessage.get("action") == "loadJSMod":
                            logger.debug("Mod loaded successfuly.")
                            # TODO: Handle success of loading mod
                            if replyMessage.get("requestId") != requestId:
                                raise RPCError(f"Invalid request ID: {replyMessage['requestId']}")
                            loadedMods.append(replyMessage.get("data"))
                            break # Loading next mod
                        elif replyMessage.get("type") == "frontendError" and replyMessage.get("action") == "loadJSMod":
                            logger.debug("Mod failed to load.")
                            # TODO: Handle failure of loading mod
                            logger.error(f"Failed to load mod: {replyMessage['details']}")
                            raise ModLoadingError(f"Failed to load mod: {replyMessage['details']}")
                        else:
                            logger.debug("Caching message for later.")
                            # This is a message we do not want to handle, so cache it
                            cachedMessages.append(replyMessage)

                # TODO: Check for mods with same name but different paths

                # 4.3. Activate mods.
                activatedMods = []
                for manifest in loadedMods:
                    print(f"Loading mod {manifest.get('modId')}")
                    requestId = f"activateJSMod_{manifest.get('modId')}"
                    await resilientWs.send({
                        "type": "backendRequest",
                        "action": "activateJSMod",
                        "data": {
                            "modId": manifest.get('modId'),
                        },
                        "clientId": view.clientId,
                        "viewId": view.viewId,
                        "securityToken": view.securityToken,
                        "timestamp": int(time.time() * 1000),
                        "requestId": requestId,
                    })

                    while True:
                        # TODO: Add timeout for awaiting activateJSMod action
                        replyMessage = await resilientWs.receive()
                        logger.debug(f"Reveived message while waiting for activateJSMod reply: {replyMessage}")
                        
                        if replyMessage.get("type") == "frontendReply" and replyMessage.get("action") == "activateJSMod":
                            logger.debug(f"Mod '{manifest.get('modId')}' activated successfuly.")
                            # TODO: Handle success of loading mod
                            if replyMessage.get("requestId") != requestId:
                                raise RPCError(f"Invalid request ID: {replyMessage['requestId']}")
                            activatedMods.append(replyMessage.get("data"))
                            break # Loading next mod
                        elif replyMessage.get("type") == "frontendError" and replyMessage.get("action") == "activateJSMod":
                            logger.debug(f"Mod '{manifest.get('modId')}' failed to load.")
                            # TODO: Handle failure of loading mod
                            logger.error(f"Failed to load mod '{replyMessage.get('error').get('modId', manifest.get('modId'))}': code={replyMessage.get('error').get('code')} reason={replyMessage.get('error').get('message')}")
                            break # Loading next mod
                        else:
                            logger.debug("Caching message for later.")
                            # This is a message we do not want to handle, so cache it
                            cachedMessages.append(replyMessage)
                
                # 4.4. Announce loading of mods finished.
                await resilientWs.send({
                    "type": "backendEmit",
                    "action": "modLoadingFinished",
                    "clientId": view.clientId,
                    "viewId": view.viewId,
                    "securityToken": view.securityToken,
                    "timestamp": int(time.time() * 1000),
                })

                # 4.5. Create main session or if frontend lost connection, refresh session.
                await view.createOrRefreshMainSession()

            # 5. Replay cached messages we received during loading and activating mods.
            for cachedMessage in cachedMessages:
                logger.debug(f"[viewws: ({viewId},{clientId})] Replaying cached message: {cachedMessage}")
                try:
                    if cachedMessage.get("type") == "frontendRequest":
                        await view.handleRequest(cachedMessage)
                    elif cachedMessage.get("type") == "frontendEmit":
                        await view.handleEmit(cachedMessage)
                    elif cachedMessage.get("type") in ("frontendReply", "frontendError"):
                        await view.handleReplyOrError(cachedMessage)
                    else:
                        logger.warning(f"[viewws: ({viewId},{clientId})] Unknown type of cached message: {cachedMessage}")
                except Exception:
                    logger.exception(f"[viewws: ({viewId},{clientId})] Error while replaying cached message.")
            
            cachedMessages.clear()

            # 6. Start listening for messages.
            while True:
                try:
                    message = await resilientWs.receive()
                    
                    logger.debug(f"[viewws: ({viewId},{clientId})] Received payload: {message}")

                    if "type" not in message:
                        logger.error("Invalid payload received. Field 'type' is required.")
                        continue

                    if message["type"] == "frontendRequest":
                        if "action" not in message:
                            logger.error("Invalid payload received. Field 'action' is required.")
                            continue

                        await view.handleRequest(message)
                    elif message["type"] == "frontendEmit":
                        if "action" not in message:
                            logger.error("Invalid payload received. Field 'action' is required.")
                            continue

                        await view.handleEmit(message)
                    elif message["type"] in ("frontendReply", "frontendError"):
                        await view.handleReplyOrError(message)
                    else:
                        logger.warning(f"[viewws: ({viewId},{clientId})] Websocket loop received unknown message type: {message.get('type')}")
                
                except WebSocketDisconnect:
                    # TODO: Handle this - reload, flush, restart?
                    logger.warning(f"[viewws: ({viewId},{clientId})] WebSocket disconnected.")
                    break
                except Exception:
                    logger.exception(f"[viewws: ({viewId},{clientId})] Error occured in websocket loop.")
        finally:
            # Cleanup
            if view is not None:
                view.assignSocket(None)

server = Server()

