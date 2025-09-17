from __future__ import annotations
import logging

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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, ValidationError, Field, ConfigDict, JsonValue
from pydantic.alias_generators import to_camel
from pathlib import Path
from typing import Any, Literal, Callable
import json5, os, time, asyncio, uuid6, re

BACKEND_DIR = Path(__file__).parent
ROOT_DIR = BACKEND_DIR.parent
WEBROOT = ROOT_DIR / "frontend"

app = FastAPI()

# ---------- Load settings (defaults + optional file) ----------
SETTINGS_DEFAULT_PATH = BACKEND_DIR / "settings_default.json5"
SETTINGS = json5.loads(SETTINGS_DEFAULT_PATH.read_text()) if SETTINGS_DEFAULT_PATH.exists() else {
    "loadedFromBackendDefaults": True,
    "protocol": {"ackWaitMs": 250, "graceWindowMs": 150, "maxInFlightPerLane": 64, "heartbeatMs": 5000},
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
        "allowList": ["httpbin.org", "api.openai.com", "localhost", "127.0.0.1"],
        "buckets": { "default": {"rpm": 600, "burst": 200}},
    },
    "debug": {"backend":  {"rpc": {"incomingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"]},
                                   "outgoingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"]}}},
              "frontend": {"rpc": {"incomingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"]},
                                   "outgoingMessages": {"log": False, "ignoreTypes": ["ack", "heartbeat"]}}},
    },
}

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
    return {"ok": True, "ts": int(time.time() * 1000)}

class ModManifest(BaseModel):
    id: str
    name: str
    version: str
    entry: str = "main.js"
    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)

class RPCMessage(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    v: str
    id: str
    type: str
    correlatesTo: str | None = None
    lane: str
    ts: int
    gen: int
    budgetMs: int | None = None
    ackOf: int | None = None
    job: dict | None = None
    idempotencyKey: str | None = None
    route: dict | None = None
    args: list | None = None
    op: str | None = None
    seq: int | None = None
    path: str | None = None
    origin: dict | None = None # For metadata only, not for auth
    chunkNo: int | None = None # For streamed payload
    final: int | None = None   # For streamed payload
    payload: dict = Field(default_factory=dict)

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
    
    # Resolve with string=False to avoid raising if file doesn't exist yet
    resolved = raw.resolve(strict=False)
    rootResolved = root.resolve(strict=True)
    
    # Must remain inside the mod root
    if not resolved.is_relative_to(rootResolved):
        raise HTTPException(403, "Mod path points outside of mod root directory")

    if not loadSettings().get("mods", {}).get("allowSymlinks", False):
        # Leaf itself must not be a symlink either
        if resolved.is_symlink():
            raise HTTPException(403, "Mod file symlink not allowed")
        # Parent chain must not include symlinks
        path = raw
        while True:
            if path.is_symlink():
                raise HTTPException(403, "Mod path symlinks not allowed")
            if path.parent == path:
                break
            path = path.parent
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
                logger.warning("Skipping mod dir without manifest: %s", dir)
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
        modManifests.append({
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "entry": f"/mods/load/{manifest.id}/{manifest.entry}",
            "permissions": manifest.permissions,
            "capabilities": manifest.capabilities,
        })
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

class Session:
    """
    Holds per-connection state: idempotency cache, pending jobs, etc.
    """
    def __init__(self):
        self.idCache: set[str] = set()
        self.replyCache: dict[str, RPCMessage] = {}
        self.pending: dict[str, asyncio.Task] = {}
        self.cancelled: set[str] = set()
        self.subscriptions: dict[str, asyncio.Task] = {} # correlatesTo -> task
    
    def dedupeKey(self, msg: RPCMessage) -> str:
        return msg.idempotencyKey or msg.id

    def remember(self, key: str):
        self.idCache.add(key)
        # Optional: prune LRU; keep it simple here

def nowMonotonicMs() -> int:
    # TODO: just demo
    try:
        import time as _t
        return int(_t.perf_counter() * 1000)
    except Exception:
        return int(time.time() * 1000)

def ackFor(msg: RPCMessage, remainingMs: int | None = None) -> RPCMessage:
    ackMsg = RPCMessage(
        id=str(uuid6.uuid7()),
        v="0.1",
        type="ack",
        correlatesTo=msg.id,
        lane=msg.lane or "sys",
        ts=nowMonotonicMs(),
        gen=0,
        budgetMs=loadSettings()["protocol"]["ackWaitMs"],
        seq=0,
        ackOf=msg.seq if msg.seq is not None else 0,
    )

    if remainingMs is not None:
        ackMsg.job = {"accepted": True, "remainingMs": max(0, int(remainingMs))}
    return ackMsg

def errorFor(msg: RPCMessage, code: str, message: str, retryable: bool=False) -> RPCMessage:
    return RPCMessage(
        id=str(uuid6.uuid7()),
        v="0.1",
        type="error",
        correlatesTo=msg.id,
        lane=msg.lane or "sys",
        ts=nowMonotonicMs(),
        gen=0,
        payload={
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    )

def replyFor(msg: RPCMessage, payload: dict) -> RPCMessage:
    return RPCMessage(
        id=str(uuid6.uuid7()),
        v="0.1",
        type="reply",
        correlatesTo=msg.id,
        lane=msg.lane or "sys",
        ts=nowMonotonicMs(),
        gen=0,
        payload=payload,
    )

def safeJsonDumps(obj: object | RPCMessage) -> str:
    if isinstance(obj, RPCMessage):
        return json5.dumps(
            obj.model_dump(by_alias=True, exclude_unset=True),
            indent=None,
            ensure_ascii=False,
            allow_nan=False,
            allow_duplicate_keys=True,
            trailing_commas=False,
            quote_keys=True,
            separators=(",", ":"),
        )
    return json5.dumps(
        obj,
        indent=None,
        ensure_ascii=False,
        allow_nan=False,
        allow_duplicate_keys=True,
        trailing_commas=False,
        quote_keys=True,
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
        logOutgoingStr(self._shorten(safeJsonDumps(data)))
        await self._ws.send_json(data, mode=mode)

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

@app.websocket("/ws")
async def wsEndpoint(ws_: WebSocket):
    ws = LoggingWebSocket(ws_)
    await ws.accept()
    sess = Session()
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
            if msgType is None:
                logger.warning(f"Received message where msg.type is None.")
                raise HTTPException(status_code=400, detail="Invalid message type")

            msgReceivedTime = nowMonotonicMs()
            timeBudget = int(msg.budgetMs if msg.budgetMs is not None else 3000)

            # Immediate ack for non-control messages
            if msgType not in ("ack", "heartbeat", "hello"):
                elapsed = nowMonotonicMs() - msgReceivedTime
                remaining = max(0, timeBudget - elapsed) if msgType  == "request" else None
                await ws.send_text(safeJsonDumps(ackFor(msg, remainingMs=remaining if msgType=="request" else None)))

            # Cancel request or subscription
            if msgType == "cancel" or msgType == "unsubscribe":
                corrId = msg.correlatesTo
                if corrId and corrId in sess.pending:
                    sess.cancelled.add(corrId)
                    sess.pending[corrId].cancel()
                    sess.pending.pop(corrId, None)
                if corrId and corrId in sess.subscriptions:
                    sess.subscriptions[corrId].cancel()
                    sess.subscriptions.pop(corrId, None)
                continue

            if msgType == "subscribe" and (msg.route if msg.route is not None else {}).get("capability") == "gm.world@1":
                async def streamWorld(corrId: str, lane: str):
                    try:
                        while True:
                            await asyncio.sleep(2.0)
                            payload = {"turn": int(time.time()), "actors": ["goblin", "player"]}
                            await ws.send_text(safeJsonDumps(RPCMessage(
                                v="0.1",
                                id=str(uuid6.uuid7()),
                                type="stateUpdate",
                                correlatesTo=corrId,
                                lane=lane,
                                ts=nowMonotonicMs(),
                                gen=0,
                                payload=payload,
                            )))
                    except asyncio.CancelledError:
                        pass
                corrId = msg.id
                lane = msg.lane or "sys"
                task = asyncio.create_task(streamWorld(corrId, lane))
                sess.subscriptions[corrId] = task
                continue

            # Dedupe
            key = sess.dedupeKey(msg)
            if key in sess.idCache and msgType in ("request", "emit"):
                # Resend cached reply if any
                cached = sess.replyCache.get(key)
                if cached:
                    await ws.send_text(safeJsonDumps(cached))
                continue
            if msgType in ("request", "emit"):
                sess.remember(key)

            if msgType == "request":
                capability = (msg.route if msg.route is not None else {}).get("capability")
                if capability == "http.client@1":
                    async def doHttp():
                        from .http_client import request as httpRequest
                        import urllib.parse, ipaddress

                        args = msg.args or []
                        if len(args) < 2:
                            await ws.send_text(safeJsonDumps(errorFor(msg, "BAD_REQUEST", "Need method, url.")))
                            return
                        method, url = args[0], args[1]
                        opts = (args[2] if len(args) > 2 else {}) or {}
                        method = str(method).upper()
                        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
                            await ws.send_text(safeJsonDumps(errorFor(msg, "METHOD_NOT_ALLOWED", f"Method {method} not allowed.")))
                            return
                        
                        # Validate against allowlist
                        try:
                            parsedUrl = urllib.parse.urlparse(url)
                            host = parsedUrl.hostname or ""
                            if not host:
                                raise ValueError("No hostname.")
                            settings = loadSettings()
                            httpProxy = settings.get("httpProxy", {})
                            allowedHosts = httpProxy.get("allowList", [])
                            if host not in allowedHosts:
                                # If host is an IP, allow only if explicitly in allow list
                                try:
                                    ipaddress.ip_address(host)
                                    # IP not in allow list: reject
                                    await ws.send_text(safeJsonDumps(errorFor(msg, "FORBIDDEN_HOST", f"Host {host} not allowed.")))
                                    return
                                except ValueError:
                                    # hostname not allowed
                                    await ws.send_text(safeJsonDumps(errorFor(msg, "FORBIDDEN_HOST", f"Host {host} not allowed.")))
                                    return
                        except Exception as err:
                            await ws.send_text(safeJsonDumps(errorFor(msg, "BAD_URL", str(err))))
                            return

                        timeoutCap = loadSettings().get("http", {}).get("timeoutCapMs", 30000)
                        timeoutMs = min(int(msg.budgetMs if msg.budgetMs is not None else 3000), int(timeoutCap))

                        # Apply settings policy for request headers
                        requestPolicy = _pickPolicy(loadSettings(), kind="requestHeaders", cap="http.client@1", host=host)
                        requestHeaders = opts.get("headers", {}) or {}
                        if not isinstance(requestHeaders, dict): requestHeaders = {} # Prevent invalid value
                        requestHeaders = _filterHeaders(requestHeaders, requestPolicy)

                        try:
                            response = await httpRequest(
                                method,
                                url,
                                headers=requestHeaders,
                                json=opts.get("json"),
                                data=opts.get("data"),
                                timeoutMs=timeoutMs,
                                retries=int(loadSettings().get("http", {}).get("retry", 2)),
                                backoffBaseMs=int(loadSettings().get("http", {}).get("backoff", {}).get("baseMs", 250)),
                                backoffMaxMs=int(loadSettings().get("http", {}).get("backoff", {}).get("maxMs", 1000)),
                            )

                            # Apply settings policy for response headers
                            responsePolicy = _pickPolicy(loadSettings(), kind="responseHeaders", cap="http.client@1", host=host)
                            responseHeaders = response["headers"]
                            if not isinstance(responseHeaders, dict): responseHeaders = {} # Prevent malformed headers
                            responseHeaders = _filterHeaders(responseHeaders, responsePolicy)
                            
                            reply = replyFor(msg, {
                                "status": response["status"],
                                "headers": responseHeaders,
                                "text": response["text"]
                            })
                            
                            await ws.send_text(safeJsonDumps(reply))
                        except Exception as err:
                            await ws.send_text(safeJsonDumps(errorFor(msg, "HTTP_ERROR", str(err), retryable=True)))
                            return
                    
                    task = asyncio.create_task(doHttp())
                    sess.pending[msg.id] = task
                    task.add_done_callback(lambda _t: sess.pending.pop(msg.id, None))
                    continue

                if capability == "gm.narration@1":
                    async def handle():
                        start = nowMonotonicMs()
                        try:
                            # Simulate some works within budget
                            toSleep = min(0.2, timeBudget/1000)
                            await asyncio.sleep(toSleep)
                            if msg.id in sess.cancelled:
                                # best effort cancel
                                return
                            action = (msg.args or ["(silence)"])[0]
                            text = f"The GM considers your action: {action!r} and responds with a twist."
                            rep = replyFor(msg, {"text": text, "spentMs": nowMonotonicMs() - start})
                            sess.replyCache[key] = rep
                            await ws.send_text(safeJsonDumps(rep))
                        except asyncio.CancelledError:
                            pass
                    task = asyncio.create_task(handle())
                    sess.pending[msg.id] = task
                    task.add_done_callback(lambda _t: sess.pending.pop(msg.id, None))
                    continue

                logger.warning(f"Received message with 'request' but : {msg}")
                await ws.send_text(safeJsonDumps(errorFor(msg, "CAPABILITY_NOT_FOUND", "Unknown capability/path")))

            if msg.type == "emit":
                capability = (msg.route if msg.route is not None else {}).get("capability")
                if capability == "test.sendText@1":
                    logger.info('GOT REQUEST TO SEND TEXT TO FRONTEND')
                    await ws.send_text(safeJsonDumps(RPCMessage(
                        v="0.1",
                        id=str(uuid6.uuid7()),
                        type="request",
                        correlatesTo=msg.id,
                        lane=msg.lane or "sys",
                        route={"capability": "ui.toast@1"},
                        op="call",
                        ts=nowMonotonicMs(),
                        gen=0,
                        payload={ "text": "Hello from backend!" }
                    )))
                if capability == "test.subscribe@1":
                    logger.info('GOT REQUEST TO SUBSCRIBE TO FRONTEND')
                    await ws.send_text(safeJsonDumps(RPCMessage(
                        v="0.1",
                        id=str(uuid6.uuid7()),
                        type="subscribe",
                        correlatesTo=msg.id,
                        lane=msg.lane or "sys",
                        route={"capability": "time.service@1"},
                        op="call",
                        ts=nowMonotonicMs(),
                        gen=0,
                        payload={}
                    )))
                if capability == "test.unsubscribe@1":
                    logger.info('GOT REQUEST TO UNSUBSCRIBE FROM FRONTEND')
                    await ws.send_text(safeJsonDumps(RPCMessage(
                        v="0.1",
                        id=str(uuid6.uuid7()),
                        type="unsubscribe",
                        correlatesTo=msg.id,
                        lane=msg.lane or "sys",
                        route={"capability": "time.service@1"},
                        op="call",
                        ts=nowMonotonicMs(),
                        gen=0,
                        payload={}
                    )))

    except WebSocketDisconnect:
        # Cleanup pending tasks
        for task in list(sess.pending.values()):
            task.cancel()
        for task in list(sess.subscriptions.values()):
            task.cancel()
        return

app.mount("/", StaticFiles(directory=WEBROOT, html=True), name="web")

def _pickPolicy(
        settings: dict,
        *,
        kind: Literal["requestHeaders","responseHeaders"],
        cap: str | None = None,
        host: str | None = None ) -> dict:
    httpProxy = settings.get("httpProxy", {})
    policy = dict(httpProxy.get(kind, {})) # Base policy

    def overlay(src: dict | None):
        nonlocal policy
        if not src: return
        policy = {**policy, **{key: value for key, value in src.items() if key in ("mode", "list")}}
    
    perCap  = (policy.get("perCap")  or {}) if isinstance(policy.get("perCap"),  dict) else {}
    perHost = (policy.get("perHost") or {}) if isinstance(policy.get("perHost"), dict) else {}

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
        lKey = key.lower()
        if(mode == "allow" and lKey in listed) or (mode == "block" and lKey not in listed):
            out[key] = value
    return out
