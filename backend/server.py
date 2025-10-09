from __future__ import annotations
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel, ValidationError, Field, ConfigDict, JsonValue, model_validator
from pydantic.alias_generators import to_camel
from pathlib import Path
from typing import Any, Literal
from collections.abc import AsyncIterator, Callable # pyright: ignore[reportShadowedImports]
import json5, os, time, asyncio, uuid6, re, secrets, hashlib, importlib.util

from core.logger import configureLogging
configureLogging()
logger = logging.getLogger(__name__)

import datetime
current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
logger.info(f"| {current_time} | Starting up backend...")

# Disable propagation from common libraries
for name in [
    "uvicorn", "uvicorn.access", "uvicorn.error",
    "fastapi", "concurrent.futures", "asyncio",
    "httpcore.connection", "httpcore.http11",
    "httpx"
]:
    logging.getLogger(name).propagate = False



BACKEND_DIR = Path(__file__).parent
ROOT_DIR = BACKEND_DIR.parent
WEBROOT = ROOT_DIR / "frontend"

@asynccontextmanager
async def life(app: FastAPI) -> AsyncIterator[None]:
    # startup
    yield
    # shutdown
    if hasattr(LLM, "aclose"):
        res = LLM.aclose()
        if asyncio.iscoroutine(res):
            await res

app = FastAPI(lifespan=life)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Load settings (defaults + optional file) ----------
SETTINGS_DEFAULT_PATH = BACKEND_DIR / "settings_default.json5"
SETTINGS = json5.loads(SETTINGS_DEFAULT_PATH.read_text()) if SETTINGS_DEFAULT_PATH.exists() else {
    "__source": "BACKEND_DEFAULTS",
    "protocol": {"ackWaitMs": 250, "graceWindowMs": 150, "maxInFlightPerLane": 64, "heartbeatMs": 5000, "maxQueue": 1024, "maxOfflineQueue": 2000},
    "reconnect": {"initialDelayMs": 500, "maxDelayMs": 15000, "factor": 2.0, "jitterRatio": 0.25},
    "timeouts": {"classes": {
        "request.fast":   {"serviceTtlMs": 800,  "clientPatienceExtraMs": 150},
        "request.medium": {"serviceTtlMs": 3000, "clientPatienceExtraMs": 200},
        "request.heavy":  {"serviceTtlMs": 30000,"clientPatienceExtraMs": 250}}
    },
    "streams": {"default": {"targetHz": 10, "maxQueueMs": 200, "coalesce": "drop-oldest"}},
    "http": {"retry": 2, "backoff": {"baseMs": 250, "maxMs": 1000, "jitterPct": 30}, "timeoutCapMs": 30000},
    "mods": {"allowSymlinks": False},
    "httpProxy": {
        "allowList": ["httpbin.org", "api.openai.com", "localhost", "127.0.0.1", "::1"],
        "buckets": { "default": {"rpm": 600, "burst": 200}},
    },
    "debug": {"backend":  {"rpc": {"maxPreviewChars": 1_000_000,
                                   "incomingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"]},
                                   "outgoingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"], "rules": [{"type": "stateUpdate", "shouldLog": True, "tests": [{"property": "payload.done", "op": "notExists", "value": True, "shouldLog": False}]}]}}},
              "frontend": {"rpc": {"maxPreviewChars": 1_000_000,
                                   "incomingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"], "rules": [{"type": "stateUpdate", "shouldLog": True, "tests": [{"property": "payload.done", "op": "notExists", "value": True, "shouldLog": False}]}]},
                                   "outgoingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"]}}}
    },
}



def quickImport(path: str | Path):
    """Dynamically import a Python file by path. Development helper only for quick testing."""
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



def deepMerge(aDict: dict, bDict: dict) -> dict:
    outDict = dict(aDict)
    for key, value in bDict.items():
        if isinstance(value, dict) and isinstance(outDict.get(key), dict):
            outDict[key] = deepMerge(outDict[key], value)
        else:
            outDict[key] = value
    return outDict



def loadUserSettings() -> JsonValue:
    filePath = Path(os.path.expanduser("~/.turnix/turnix.json5"))
    if filePath.exists():
        try:
            return json5.loads(filePath.read_text())
        except Exception as err:
            logger.error("Failed to parse %s: %s", filePath, err)
    return {}



def loadSettings():
    merged = deepMerge(SETTINGS, loadUserSettings())
    return merged



@app.get("/settings")
async def getSettings():
    return JSONResponse(loadSettings())



@app.get("/health")
async def health():
    # TODO: add llama.cpp or other driver health here too
    return {"ok": True, "ts": int(time.time() * 1000)}



def nowMonotonicMs() -> int:
    try:
        import time as _t
        return int(_t.perf_counter() * 1000)
    except Exception:
        return int(time.time() * 1000)



def sha256sumWithPath(path: str | Path) -> str:
    path = Path(path).resolve()
    sha = hashlib.sha256()

    # Include absolute path in the hash
    sha.update(str(path).encode("utf-8"))

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            sha.update(chunk)
    
    return sha.hexdigest()



class ModManifest(BaseModel):
    id: str
    name: str
    version: str
    entry: str = "main.js"
    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)



class Gen(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )
    num: int
    salt: str



class Route(BaseModel):
    capability: str | None = None
    object: str | None = None



class Invocation(BaseModel):
    route: Route
    path: str | None = None
    op: str | None = None
    args: list | None = None



class RPCMessage(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    v: str                          # RPCMessage schema version
    id: str                         # UUIDv7
    type: Literal["ack","hello","welcome","clientReady","request","emit","reply","subscribe","stateUpdate","unsubscribe","cancel","error"]
    correlatesTo: str | None = None # UUIDv7 of previous message, if in sequence.
    gen: Gen                        # generation of connection as set by server
    ts: int = Field(default_factory=nowMonotonicMs) # Monotonic time of sending
    budgetMs: int | None = None     # How many ms to finish job and communication
    ackOf: int | None = None
    job: dict[str, Any] | None = None # Represents current status of job being executed
    idempotencyKey: str | None = None
    route: Route | None = None      # "Address" of handler which should be handling the message
    op: str | None = None           # "operation" handler should perform, if further specification is needed
    path: str | None = None         # Additional info for handler to decide which "operation" to execute
    args: list[Any] | None = None   # "arguments" for "operation" handler might find useful to decide what "operation" to execute
    seq: int | None = None          # Per-lane delivery sequence number
    origin: dict[str, Any] | None = None # For metadata only, not for auth
    chunkNo: int | None = None      # For streamed payload
    final: int | None = None        # For streamed payload
    payload: dict[str, Any] = Field(default_factory=dict)
    
    # Non-optional with a default value
    lane: str = Field(default="noLaneSet") # "sys" or other lane name

    # --------------
    #   Validators  
    # --------------
    @model_validator(mode="after")
    def fillDefaults(self):
        # lane fallback based on route
        if not self.lane or self.lane == "noLaneSet":
            if self.route:
                if self.route.capability is not None:
                    self.lane = f"cap:{self.route.capability}"
                elif self.route.object is not None:
                    self.lane = f"obj:{self.route.object}"
                else:
                    self.lane = "noValidRouteLane"
            else:
                self.lane = "noLaneSet"
        
        return self


def defaultModRoots() -> list[tuple[Path, dict[str, Any]]]:
    roots: list[tuple[Path, dict[str, Any]]] = []
    roots.append((ROOT_DIR / "mods", {"writable": False, "trust": "unsigned-ok"}))
    usermods = Path(os.path.expanduser("~/Documents/My Games/Turnix/mods"))
    usermods.mkdir(parents=True, exist_ok=True)
    roots.append((usermods, {"writable": True, "trust": "unsigned-ok"}))
    return roots

MOD_ROOTS = defaultModRoots()



def resolveSafe(root: Path, requested: str) -> Path:
    raw = root.joinpath(requested)
    
    # Resolve with strict=False to avoid raising if file doesn't exist yet
    resolved = raw.resolve(strict=False)
    rootResolved = root.resolve(strict=True)
    
    # Must remain inside the mod root
    if not resolved.is_relative_to(rootResolved):
        raise HTTPException(403, "Mod path points outside of mod root directory")

    if not loadSettings().get("mods", {}).get("allowSymlinks", False):
        # Leaf itself must not be a symlink either
        if resolved.is_symlink():
            raise HTTPException(403, "Mod file symlink not allowed")
        
        # If caller is the root itself(requested "", ".", or "/"), skip the walk.
        if resolved == rootResolved:
            return resolved
        
        # Parent chain must not include symlinks
        path = raw
        while True:
            if path.is_symlink():
                raise HTTPException(403, "Mod path symlinks not allowed")
            
            if path.resolve(strict=False) == rootResolved:
                break

            parent = path.parent
            if parent == path: # Filesystem root guard
                break
            path = parent
    return resolved



def findManifestPath(dir: Path) -> Path|None:
    if(dir / "mod.json5").exists():
        return dir / "mod.json5"
    if(dir / "mod.json").exists():
        return dir / "mod.json"
    return None



def scanMods() -> dict[str, tuple[Path, Path, ModManifest, str]]:
    found: dict[str, tuple[Path, Path, ModManifest, str]] = {}
    for root, _cfg in MOD_ROOTS:
        if not root.exists():
            continue
        for dir in root.iterdir():
            if not dir.is_dir():
                continue
            manifestPath = findManifestPath(dir)
            if not manifestPath:
                logger.info("Skipping mod dir without manifest: %s", dir)
                continue
            try:
                raw = json5.loads(manifestPath.read_text())
                manifest = ModManifest.model_validate(raw)
                found[manifest.id] = (root, dir, manifest, manifestPath.name)
            except ValidationError as verr:
                logger.error("Invalid manifest in %s: %s", manifestPath, verr)
            except Exception as err:
                logger.exception("Failed reading manifest %s: %s", manifestPath, err)
    return found



@app.get("/mods/index")
def listMods():
    found = scanMods()
    logger.info("Mods discovered: %d", len(found))
    modManifests = []
    for _modId, (_root, _dir, manifest, _fname) in found.items():
        entry = f"/mods/load/{manifest.id}/{manifest.entry}"
        modEntry ={
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "entry": entry,
            "permissions": manifest.permissions,
            "capabilities": manifest.capabilities,
        }
        entryPath = _dir / manifest.entry
        if not entryPath.exists():
            logger.warning("Missing entry file for mod '%s': '%s'", manifest.id, str(entryPath))
            modEntry["problems"] = modEntry.get("problems", [])
            modEntry["problems"].append({"error": f"Entry file not found."})
            modEntry["enabled"] = False
        else:
            modEntry["hash"] = sha256sumWithPath(_dir / manifest.entry)
            modEntry["enabled"] = True
        modManifests.append(modEntry)
    return {"modManifests": modManifests}



@app.get("/mods/validate")
def validateMods():
    results = []
    for root, _cfg in MOD_ROOTS:
        if not root.exists(): continue
        for dir in root.iterdir():
            if not dir.is_dir(): continue
            status = {"dir": str(dir), "status": "ok", "problems": []}
            manifestPath = findManifestPath(dir)
            if not manifestPath:
                status["status"] = "missing-manifest"
                results.append(status); continue
            try:
                raw = json5.loads(manifestPath.read_text())
                manifest = ModManifest.model_validate(raw)
                entryPath = dir / manifest.entry
                if not entryPath.exists():
                    status["status"] = "bad-entry"
                    status["problems"].append(f"entry missing: {manifest.entry}")
                results.append(status)
            except ValidationError as verr:
                status["status"] = "invalid-manifest"
                status["problems"].append(str(verr))
                results.append(status)
            except Exception as err:
                status["status"] = "unknown-error"
                status["problems"].append(str(err))
                results.append(status)
    return {"results": results}



@app.get("/mods/load/{modId}/{path:path}")
def serveModAsset(modId: str, path: str):
    found = scanMods()
    if modId not in found:
        raise HTTPException(404, "Unknown mod")
    _root, moddir, _manifest, _fname = found[modId]
    safe = resolveSafe(moddir, path or "main.js")
    if not safe.exists() or not safe.is_file():
        raise HTTPException(404)
    # TODO: Add strict caching/versioning later; for now no-cache in dev
    return FileResponse(safe)



class RPCSession:
    """
    Holds per-connection state: idempotency cache, pending jobs, etc.
    """
    def __init__(self, key: tuple[str, str | None, str | None]):
        self.key = key
        self.idCache: set[str] = set()
        self.replyCache: dict[str, RPCMessage] = {}
        self.pending: dict[str, asyncio.Task] = {}
        self.cancelled: set[str] = set()
        self.subscriptions: dict[str, asyncio.Task] = {} # correlatesTo -> task
        self.state = {
            "serverMessage": "Welcome to Turnix RPC",
            "serverBootTs": time.time(),
            "mods": {
                "frontend": listMods(),
            }
        }
        self.genNum = 0
        self.genSalt = ""
        # Last clientReady payload
        self.lastClientReady: dict | None = None

    def newGeneration(self) -> dict:
        self.genNum += 1
        self.genSalt = secrets.token_hex(4)
        return {"num": self.genNum, "salt": self.genSalt}
    
    def currentGeneration(self) -> dict:
        return {"num": self.genNum, "salt": self.genSalt}

    def dedupeKey(self, msg: RPCMessage) -> str:
        return msg.idempotencyKey or msg.id

    _MAX_CACHE = 512
    def remember(self, key: str):
        self.idCache.add(key)
        if len(self.idCache) > self._MAX_CACHE:
            # simple prune: drop ~1/4
            for _ in range(len(self.idCache) // 4):
                self.idCache.pop()
    
    def putReply(self, key: str, reply: RPCMessage):
        self.replyCache[key] = reply
        if len(self.replyCache) > self._MAX_CACHE:
            # drop arbitrary 1/4
            for k in list(self.replyCache.keys())[:len(self.replyCache)//4]:
                self.replyCache.pop(k, None)



def _gen(session: RPCSession) -> Gen:
    return Gen.model_validate(session.currentGeneration())



def safeJsonDumps(obj: object | RPCMessage) -> str:
    import json
    if isinstance(obj, RPCMessage):
        return json.dumps(
            obj.model_dump(by_alias=True, exclude_unset=True),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    return json.dumps(
        obj,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )

# ---------- WebSocket RPC (minimal echo-capable with ACK) ----------

def logIncomingStr(text: str) -> None:
    if(loadSettings().get("debug", {}).get("backend", {}).get("rpc", {}).get("incomingMessages", {}).get("log", False)):
        ignoreTypes = loadSettings().get("debug", {}).get("backend", {}).get("rpc", {}).get("incomingMessages", {}).get("ignoreTypes", [])
        if all(f'"type":"{value}"' not in text for value in ignoreTypes):
            logger.debug(f"[RPC] incoming: {text}")



def logOutgoingStr(text: str) -> None:
    if(loadSettings().get("debug", {}).get("backend", {}).get("rpc", {}).get("outgoingMessages", {}).get("log", False)):
        ignoreTypes = loadSettings().get("debug", {}).get("backend", {}).get("rpc", {}).get("outgoingMessages", {}).get("ignoreTypes", [])
        if all(f'"type":"{value}"' not in text for value in ignoreTypes):
            logger.debug(f"[RPC] sending: {text}")



def defaultRedactor(text: str) -> str:
    # Hide bearer tokens & simple passwords
    # TODO: add more protection of private data
    text = re.sub(r"Bearer\s+[A-Za-z0-9\._\-]+", "Bearer ***", text)
    text = re.sub(r'("password"\s*:\s*")[^"]+(")', r'\1***\2', text)
    return text



class LoggingWebSocket:
    """
    Proxy around fastapi.WebSocket that logs outgoing messages.
    Only overrides send_text/send_json/send_bytes; everything else is delegated.
    """
    def __init__(self, ws: WebSocket, *, maxLen: int = 4096, redact: Callable[[str], str] = defaultRedactor):
        self._ws = ws
        self._maxLen = maxLen
        self._redact = redact
    
    async def send_text(self, data: str) -> None:
        logOutgoingStr(self._shorten(data))
        await self._ws.send_text(data)
    
    async def send_json(self, data: Any, mode: str = "text") -> None:
        if mode not in ("text", "binary"):
            raise ValueError("mode must be 'text' or 'binary'")
        
        if mode == "text":
            if isinstance(data, str):
                payloadText = data
            elif isinstance(data, (bytes, bytearray)):
                payloadText = data.decode("utf-8", errors="replace")
            else:
                payloadText = safeJsonDumps(data)
            logOutgoingStr(self._shorten(payloadText))
            await self._ws.send_text(payloadText)
        
        else: # binary mode
            if isinstance(data, (bytes, bytearray)):
                payloadBytes = bytes(data)
            elif isinstance(data, str):
                payloadBytes = data.encode("utf-8")
            else:
                payloadBytes = safeJsonDumps(data).encode("utf-8")
            preview = payloadBytes[:self._maxLen or 4096]
            logOutgoingStr(self._shorten(preview.decode("utf-8", errors="replace")))
            await self._ws.send_bytes(payloadBytes)

    async def send_bytes(self, data: bytes) -> None:
        logOutgoingStr(self._shorten(f"<{len(data)} bytes>"))
        await self._ws.send_bytes(data)

    def _shorten(self, payload: str) -> str:
        payload = self._redact(payload)
        if self._maxLen and len(payload) > self._maxLen:
            return payload[:self._maxLen] + "…"
        return payload
    
    def __getattr__(self, name: str):
        return getattr(self._ws, name)



RPC_SESSIONS: dict[tuple[str, str | None, str | None], RPCSession] = {}



def getSession(viewId: str, clientId: str | None, sessionId: str | None) -> RPCSession:
    key = (viewId, clientId, sessionId)
    session = RPC_SESSIONS.get(key)
    if not session:
        session = RPCSession(key)
        RPC_SESSIONS[key] = session
    return session



def uuidv7() -> str:
    return str(uuid6.uuid7())



def createWelcomeMessage(props: dict[str, Any], opts: dict[str, Any] | None = None):
    if not isinstance(props, dict): raise TypeError("props must be a dict")
    
    try:
        gen = Gen.model_validate(props["gen"])
    except KeyError:
        raise ValueError("props.gen is required")
    except ValidationError as err:
        raise TypeError(f"props.gen must be a valid Gen: {err}")
    
    payload = props.get("payload", {})
    if not isinstance(payload, dict): raise TypeError("props.payload must be a dict")

    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="welcome",
        ts=nowMonotonicMs(),
        gen=gen,
        lane="sys",
        budgetMs=pickBudgetMs(opts),
        payload=payload,
    )



def createAckMessage(toMsg: RPCMessage, props: dict[str, Any]):
    if not isinstance(toMsg, RPCMessage): raise TypeError("toMsg must be a valid RPCMessage")
    if not isinstance(props, dict): raise TypeError("props must be a dict")
    
    try:
        gen = Gen.model_validate(props["gen"])
    except KeyError:
        raise ValueError("props.gen is required")
    except ValidationError as err:
        raise TypeError(f"props.gen must be a valid Gen: {err}")

    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="ack",
        budgetMs=loadSettings()["protocol"]["ackWaitMs"],
        gen=gen,
        route=toMsg.route,
        lane="sys",
        correlatesTo=toMsg.id,
        payload={},
    )



def createErrorMessage(toMsg: RPCMessage, props: dict[str, Any], opts: dict[str, Any] | None = None):
    if not isinstance(toMsg, RPCMessage): raise TypeError("toMsg must be a valid RPCMessage")
    if not isinstance(props, dict): raise TypeError("props must be a dict")
    
    try:
        gen = Gen.model_validate(props["gen"])
    except KeyError:
        raise ValueError("props.gen is required")
    except ValidationError as err:
        raise TypeError(f"props.gen must be a valid Gen: {err}")

    payload = props.get("payload", {})
    if not isinstance(payload, dict): raise TypeError("props.payload must be a dict")
    
    errorPayload = {
        "code": props.get("code", payload.get("code", "UNKNOWN_ERROR")),
        "message": props.get("message", payload.get("message", "")),
        "err": serializeError(props.get("err", payload.get("err"))),
        "retryable": props.get("retryable", payload.get("retryable", False)),
    }

    if not isinstance(errorPayload["code"], str): raise TypeError("code or payload.code must be a string with readable error code")
    
    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="error",
        gen=gen,
        route=toMsg.route,
        lane="sys",
        correlatesTo=toMsg.id,
        budgetMs=pickBudgetMs(opts),
        payload=errorPayload,
    )



def createReplyMessage(toMsg: RPCMessage, props: dict[str, Any], opts: dict[str, Any] | None = None):
    if not isinstance(toMsg, RPCMessage): raise TypeError("toMsg must be a valid RPCMessage")
    if not isinstance(props, dict): raise TypeError("props must be a dict")
    
    try:
        gen = Gen.model_validate(props["gen"])
    except KeyError:
        raise ValueError("props.gen is required")
    except ValidationError as err:
        raise TypeError(f"props.gen must be a valid Gen: {err}")
    
    payload = props.get("payload")
    if not isinstance(payload, dict): raise TypeError("props.payload must be a dict (did you forget to add reply payload?)")
    
    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="reply",
        correlatesTo=toMsg.id,
        idempotencyKey=toMsg.idempotencyKey if toMsg.idempotencyKey is not None else None,
        route=toMsg.route,
        lane=toMsg.lane,
        gen=gen,
        budgetMs=pickBudgetMs(opts),
        payload=payload,
    )



def pickBudgetMs(opts) -> int:
    # 3000 ms is fallback default
    if isinstance(opts, dict):
        budgetMs = opts.get("budgetMs") if isinstance(opts, dict) else None
        if budgetMs is None:
            budgetMs = resolveClassCfg(opts).get("serviceTtlMs", 3000)
    else:
        # Fallback to 3000
        budgetMs = 3000
    return budgetMs



def resolveClassCfg(opts) -> dict:
    cls = opts.get("class") or "request.medium"
    cfg = loadSettings().get("timeouts", {}).get("classes", {}).get(cls) or { "serviceTtlMs": 3000, "clientPatienceExtraMs": 200 }
    return cfg



async def sendRaw(ws: LoggingWebSocket, message: RPCMessage):
    await ws.send_text(safeJsonDumps(message))



llmclientMod = quickImport(Path("mods/first-party/drivers/llamacpp/llamacpp_client.py"))
LLM = llmclientMod.LlamaCppClient()



async def pushToast(ws: LoggingWebSocket, level: Literal["info", "warn", "error"], text: str, gen: dict, ttlMs: int = 5000) -> None:
    await sendRaw(ws, RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="emit",
        route=Route(capability="ui.toast@1"),
        gen=Gen.model_validate(gen),
        payload={
            "level": level,
            "text": text,
        }
    ))



@app.websocket("/ws")
async def wsEndpoint(ws_: WebSocket):
    ws = LoggingWebSocket(ws_)
    await ws.accept()
    sessLocal: RPCSession | None = None
    
    try:
        while True:
            raw = await ws.receive_text()
            logIncomingStr(raw)

            try:
                msg = RPCMessage.model_validate_json(raw)
            except ValidationError as verr:
                logger.exception(f"Invalid JSON: {verr}")
                continue
            
            msgType = msg.type

            # TODO: warn on message with lane = "noLaneSet" or "noValidRouteLane"

            # ----- Handshake -----
            if msgType == "hello":
                sessLocal = getSession("view-1", "client-1", "session-1")
                gen = sessLocal.newGeneration()

                # Send snapshot state with welcome
                await ws.send_text(safeJsonDumps(createWelcomeMessage({
                    "gen": gen,
                    "payload": {
                        "state": sessLocal.state,
                    },
                })))
                continue

            # Handshake is required!
            if sessLocal is None:
                # Ignore anything before hello
                continue

            if msgType == "clientReady":
                # Frontend declares it has finished loading/initializing
                sessLocal.lastClientReady = {
                    "gen": msg.gen,
                    "ts": msg.ts,
                    "mods": {
                        "loaded": msg.payload.get("mods", {}).get("loaded") or [],
                        "failed": msg.payload.get("mods", {}).get("failed") or [],
                        "modsHash": msg.payload.get("modsHash"),                    
                    }
                }
                await sendRaw(ws, createAckMessage(msg, {"gen": sessLocal.currentGeneration()}))
                continue

            if msgType is None:
                logger.warning(f"Received message where msg.type is None.")
                raise HTTPException(status_code=400, detail="Invalid message type")

            # Immediate ack for non-control messages
            if msgType not in ("ack", "heartbeat"):
                await sendRaw(ws, createAckMessage(msg, { "gen": sessLocal.currentGeneration() }))

            # Cancel request or subscription
            if msgType == "cancel" or msgType == "unsubscribe":
                corrId = msg.correlatesTo
                if corrId and corrId in sessLocal.pending:
                    sessLocal.cancelled.add(corrId)
                    sessLocal.pending[corrId].cancel()
                    sessLocal.pending.pop(corrId, None)
                if corrId and corrId in sessLocal.subscriptions:
                    sessLocal.subscriptions[corrId].cancel()
                    sessLocal.subscriptions.pop(corrId, None)
                continue

            if msgType == "subscribe":
                capability = (msg.route.capability if isinstance(msg.route, Route) else "") or ""
                handler = SUBSCRIBE_HANDLERS.get(capability)
                if not handler:
                    logger.warning(f"Unknown capability for subscribe: '{capability}'\n{msg}")
                    await sendRaw(ws, createErrorMessage(msg, {
                        "gen": sessLocal.currentGeneration(),
                        "payload": {"code":"CAPABILITY_NOT_FOUND","message":"Unknown capability/route for subscribe"}
                    }))
                    continue
                await handler(HandlerContext(ws=ws, session=sessLocal), msg)
                continue

            if msgType == "request":
                capability = (msg.route.capability if isinstance(msg.route, Route) else "") or ""
                handler = REQUEST_HANDLERS.get(capability)
                if not handler:
                    logger.warning(f"Unknown capability for request: '{capability}'\n{msg}")
                    await sendRaw(ws, createErrorMessage(msg, {
                        "gen": sessLocal.currentGeneration(),
                        "payload": {"code":"CAPABILITY_NOT_FOUND","message":"Unknown capability/route for request"}
                    }))
                    continue
                await handler(HandlerContext(ws=ws, session=sessLocal), msg)
                continue

            if msg.type == "emit":
                capability = (msg.route.capability if isinstance(msg.route, Route) else "") or ""
                handler = EMIT_HANDLERS.get(capability)
                if not handler:
                    logger.warning(f"Unknown capability for emit: '{capability}'\n{msg}")
                    await sendRaw(ws, createErrorMessage(msg, {
                        "gen": sessLocal.currentGeneration(),
                        "payload": {"code":"CAPABILITY_NOT_FOUND","message":"Unknown capability/route for emit"}
                    }))
                    continue
                await handler(HandlerContext(ws=ws, session=sessLocal), msg)
                continue

    except WebSocketDisconnect:
        if sessLocal is not None:
            # Cleanup pending tasks
            for task in list(sessLocal.pending.values()):
                task.cancel()
            for task in list(sessLocal.subscriptions.values()):
                task.cancel()
        return


# TODO: Make sure directory WEBROOT exists at this point, or FastAPI/Starlette throws here
app.mount("/", StaticFiles(directory=WEBROOT, html=True), name="web")



def _pickPolicy(
        settings: dict,
        *,
        kind: Literal["requestHeaders","responseHeaders"],
        cap: str | None = None,
        host: str | None = None ) -> dict:
    httpProxy = settings.get("httpProxy", {})
    base = dict(httpProxy.get(kind, {})) # Base policy
    policy = dict(base)

    def overlay(src: dict | None):
        nonlocal policy
        if not src: return
        policy = {**policy, **{key: value for key, value in src.items() if key in ("mode", "list")}}
    
    perCap  = (base.get("perCap")  or {}) if isinstance(base.get("perCap"),  dict) else {}
    perHost = (base.get("perHost") or {}) if isinstance(base.get("perHost"), dict) else {}

    overlay(perCap.get(cap))   if cap  else None
    overlay(perHost.get(host)) if host else None

    # Normalize
    lst = policy.get("list", [])
    policy["list"] = [el.lower() for el in lst] if isinstance(lst, (list, tuple)) else []
    policy["mode"] = policy.get("mode", "block")

    return policy



def _filterHeaders(headers: dict[str, str], policy: dict) -> dict[str, str]:
    mode = policy["mode"]
    listed = set(policy["list"])
    out = {}
    for key, value in (headers or {}).items():
        lKey = str(key).lower()
        if(mode == "allow" and lKey in listed) or (mode == "block" and lKey not in listed):
            out[str(key)] = str(value)
    return out



from dataclasses import dataclass
@dataclass
class HandlerContext:
    ws: LoggingWebSocket
    session: RPCSession


async def handleSubscribeGMWorld(ctx: HandlerContext, msg: RPCMessage):
    """
    Subscribe: gm.world@1
    """
    async def streamWorld(correlatesTo: str, lane: str, session: RPCSession):
        try:
            while True:
                await asyncio.sleep(2.0)
                payload = { "turn": int(time.time()), "actors": ["goblin", "player"] }
                await sendRaw(ctx.ws, RPCMessage(
                    v="0.1",
                    id=(uuidv7()),
                    type="stateUpdate",
                    correlatesTo=correlatesTo,
                    lane=lane,
                    ts=nowMonotonicMs(),
                    gen=_gen(session),
                    payload=payload,
                ))
        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.debug("gm.world stream stopped: %s", err)
            pass
    correlatesTo = msg.id
    lane = msg.lane
    task = asyncio.create_task(streamWorld(correlatesTo=correlatesTo, lane=lane, session=ctx.session))
    ctx.session.subscriptions[correlatesTo] = task

async def handleRequestHttpClient(ctx: HandlerContext, msg: RPCMessage):
    """
    Request: http.client@1
    """
    from .http_client import request as httpRequest
    import urllib.parse, base64

    args = msg.args or []
    if len(args) < 2:
        await sendRaw(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.session.currentGeneration(),
            "payload": {"code": "BAD_REQUEST", "message": "Arguments required: method, url"},
        }))
        return
    
    method, url = args[0], args[1]
    opts = (args[2] if len(args) > 2 else {}) or {}
    method = str(method).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        await sendRaw(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.session.currentGeneration(),
            "payload": {"code": "BAD_REQUEST", "message": f"Invalid HTTP method: {method}"},
        }))
        return
    
    settings = loadSettings()
    # Allowlist check
    try:
        parsedUrl = urllib.parse.urlparse(url)
        host = parsedUrl.hostname or ""
        if not host:
            await sendRaw(ctx.ws, createErrorMessage(msg, {
                "gen": ctx.session.currentGeneration(),
                "payload": {"code": "BAD_REQUEST", "message": f"Invalid URL: {url}"},
            }))
            return
        httpProxy = settings.get("httpProxy", {})
        allowedHosts = httpProxy.get("allowList", [])
        if host not in allowedHosts:
            await sendRaw(ctx.ws, createErrorMessage(msg, {
                "gen": ctx.session.currentGeneration(),
                "payload": {"code": "FORBIDDEN_HOST", "message": f"Host {host} not allowed"}
            }))
            return
    except Exception as err:
        await sendRaw(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.session.currentGeneration(),
            "payload": {"code": "BAD_URL", "message": str(err), "err": err}
        }))
        return
    
    timeoutCapMs = settings.get("http", {}).get("timeoutCapMs", 30_000)
    timeoutMs = min(int(msg.budgetMs if msg.budgetMs is not None else 3_000), int(timeoutCapMs))

    # Header policies
    requestHeadersPolicy = _pickPolicy(settings=settings, kind="requestHeaders", cap="http.client@1", host=host)
    requestHeaders = opts.get("headers", {}) or {}
    if not isinstance(requestHeaders, dict):
        requestHeaders = {}
    requestHeaders = _filterHeaders(requestHeaders, requestHeadersPolicy)

    try:
        response = await httpRequest(
            method=method,
            url=url,
            headers=requestHeaders,
            json=opts.get("json"),
            data=opts.get("data"),
            params=opts.get("params"),
            timeoutMs=timeoutMs,
            retries=int(settings.get("http", {}).get("retry", 2)),
            backoffBaseMs=int(settings.get("http", {}).get("backoff", {}).get("baseMs", 250)),
            backoffMaxMs=int(settings.get("http", {}).get("backoff", {}).get("maxMs", 1_000)),
            followRedirects=bool(opts.get("followRedirects", True)),
        )

        responseHeadersPolicy = _pickPolicy(settings=settings, kind="responseHeaders", cap="http.client@1", host=host)
        responseHeaders = response.get("headers", {}) or {}
        if not isinstance(responseHeaders, dict):
            responseHeaders = {}
        responseHeaders = _filterHeaders(responseHeaders, responseHeadersPolicy)

        if not isinstance(response.get("status"), int):
            await sendRaw(ctx.ws, createErrorMessage(msg, {
                "gen": _gen(ctx.session),
                "payload": {"code":"HTTP_ERROR","message":"Received invalid status code."},
            }))
            return
        
        content = response.get("content", b"")
        if isinstance(content, str): # Make sure we have bytes and not accidentally pass str to b64encode
            content = content.encode("utf-8", errors="replace")

        payload = {
            "status": response["status"],
            "headers": responseHeaders,
            "text": response.get("text", ""),
            "contentB64": base64.b64encode(content).decode("ascii"),
        }

        if response.get("json") is not None:
            payload["json"] = response["json"]

        await sendRaw(ctx.ws, createReplyMessage(msg, {
            "gen": ctx.session.currentGeneration(),
            "payload": payload,
        }))
        return
    except Exception as err:
        await sendRaw(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.session.currentGeneration(),
            "payload": {"code":"HTTP_ERROR","message":str(err),"err":err,"retryable": True},
        }))
        return

async def handleSubscribeChat(ctx: HandlerContext, msg: RPCMessage):
    """
    Subscribe: chat@1 (streaming, cancellable)
    """
    # Build OpenAI-style messages from payload
    userTurn = {
        "id": msg.id + ".u",
        "role": msg.payload.get("role", "user"),
        "text": msg.payload.get("text", ""),
    }

    messages = [{
        "role": ("assistant" if userTurn["role"] == "assistant" else "user"),
        "content": userTurn["text"]
    }]

    async def run():
        assistantChunks: list[str] = []
        try:
            async for event in LLM.streamChat(
                messages,
                model=msg.payload.get("model", ""),
                temperature=msg.payload.get("temperature", 0.8),
                max_tokens=msg.payload.get("max_tokens", 256),
                top_p=msg.payload.get("top_p"),
                extra=msg.payload.get("extra"),
            ):
                if msg.id in ctx.session.cancelled:
                    break

                if event.get("error"):
                    await sendRaw(ctx.ws, createErrorMessage(msg, {
                        "gen": ctx.session.currentGeneration(),
                        "payload": {"code":"ERR_LLM_STREAM", "message": event.get("error"), "err": event},
                    }))
                    break

                if event.get("done"):
                    break

                ch = (event.get("choices") or [{}])[0]
                delta = (ch.get("delta") or {}).get("content") or ""
                if not delta:
                    continue
                assistantChunks.append(delta)
                await sendRaw(ctx.ws, RPCMessage(
                    id=uuidv7(),
                    v="0.1",
                    type="stateUpdate",
                    lane=msg.lane,
                    gen=_gen(ctx.session),
                    correlatesTo=msg.id,
                    payload={"delta": delta}
                ))
        except asyncio.CancelledError:
            pass
        except Exception as err:
            await sendRaw(ctx.ws, createErrorMessage(msg, {
                "gen": ctx.session.currentGeneration(),
                "payload": {"code":"ERR_LLM", "message":str(err), "err": err, "retryable": True},
            }))
        else:
            fullText = "".join(assistantChunks)
            await sendRaw(ctx.ws, RPCMessage(
                id=uuidv7(),
                v="0.1",
                type="stateUpdate",
                lane=msg.lane,
                gen=_gen(ctx.session),
                correlatesTo=msg.id,
                payload={"text": fullText, "delta": "", "done": True},
            ))
    
    task = asyncio.create_task(run())
    ctx.session.pending[msg.id] = task
    task.add_done_callback(lambda _t, lSession=ctx.session, lMsgId=msg.id: (lSession.pending.pop(lMsgId, None), lSession.cancelled.discard(lMsgId)))

async def handleRequestGMNarration(ctx: HandlerContext, msg: RPCMessage):
    """
    Request: gm.narration@1 (simple, cancellable)
    """
    async def run():
        start = nowMonotonicMs()
        try:
            toSleep = min(0.2, (msg.budgetMs or 3_000)/1_000)
            await asyncio.sleep(toSleep)
            if msg.id in ctx.session.cancelled:
                return
            action = (msg.args or ["(silence)"])[0]
            text = f"The GM considers your action {action!r} and responds with a twist."
            reply = createReplyMessage(msg, {
                "gen": ctx.session.currentGeneration(),
                "payload": {"text": text, "spentMs": nowMonotonicMs() - start},
            })
            ctx.session.putReply(ctx.session.dedupeKey(msg), reply)
            await sendRaw(ctx.ws, reply)
        except asyncio.CancelledError:
            pass
    
    task = asyncio.create_task(run())
    ctx.session.pending[msg.id] = task
    task.add_done_callback(lambda _t, lSession=ctx.session, lMsgId=msg.id: (lSession.pending.pop(lMsgId, None), lSession.cancelled.discard(lMsgId)))

REQUEST_HANDLERS: dict[str, Callable[[HandlerContext, RPCMessage], Any]] = {
    "http.client@1":    handleRequestHttpClient,
    "gm.narration@1":   handleRequestGMNarration,
}
SUBSCRIBE_HANDLERS: dict[str, Callable[[HandlerContext, RPCMessage], Any]] = {
    "chat@1":           handleSubscribeChat,
    "gm.world@1":       handleSubscribeGMWorld,
}
EMIT_HANDLERS: dict[str, Callable[[HandlerContext, RPCMessage], Any]] = {
    
}

def serializeError(err: Any) -> dict[str, Any]:
    """Converts Exception object to JSON-serializable dict."""
    import json, traceback

    if err is None:
        return {}
    if isinstance(err, str):
        return {"message": err}
    if isinstance(err, BaseException):
        data = {
            "type": err.__class__.__name__,
            "name": err.__class__.__name__,
            "message": str(err),
            "args": [repr(arg) for arg in getattr(err, "args", [])],
        }
        traceBack = getattr(err, "__traceback__", None)
        if traceBack:
            data["stack"] = "".join(traceback.format_tb(traceBack))[-4000:] # Prevent huge frames
            if len(data["stack"]) > 4000:
                data["stack"] = data["stack"] + "[TRUNCATED]"
        return data
    try:
        # Maybe it's already serializable?
        json.dumps(err) # if not, it throws
        return err
    except Exception:
        return {"type": type(err).__name__, "repr": repr(err)}

def tryJSONify(obj: Any) -> Any:
    """Tries to convert any object to a JSON-serializable value."""
    import base64
    from datetime import date, datetime
    from pathlib import Path
    from collections.abc import Mapping, Iterable # pyright: ignore[reportShadowedImports]

    # Basic types
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    
    # Exceptions
    if isinstance(obj, BaseException):
        return serializeError(obj)

    # Bytes/bytearray
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(obj)).decode("ascii")}
    
    # Datetime/date
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()

    # Path
    if isinstance(obj, Path):
        return str(obj)
    
    # Set/tuples
    if isinstance(obj, (set, tuple)):
        return [tryJSONify(value) for value in obj]
    
    # Mappings
    if isinstance(obj, Mapping):
        return {str(key): tryJSONify(value) for key, value in obj.items()}

    # Iterables which are not handles above
    if isinstance(obj, Iterable):
        return [tryJSONify(value) for value in obj]
    
    # Fallback: string representation
    return str(obj)
