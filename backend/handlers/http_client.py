# backend/handlers/http_client.py
from __future__ import annotations
from typing import Literal, Mapping, Any

from backend.rpc.models import RPCMessage
from backend.rpc.messages import createErrorMessage, createReplyMessage
from backend.handlers.context import HandlerContext
from backend.app.globals import config



async def handleRequestHttpClient(ctx: HandlerContext, msg: RPCMessage):
    """
    Request: http.client@1
    """
    from backend.http.client import request as httpRequest
    from backend.rpc.transport import sendRPCMessage
    import urllib.parse, base64

    args = msg.args or []
    if len(args) < 2:
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcConnection.gen(),
            "payload": {"code": "BAD_REQUEST", "message": "Arguments required: method, url"},
        }))
        return
    
    method, url = args[0], args[1]
    opts = (args[2] if len(args) > 2 else {}) or {}
    method = str(method).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcConnection.gen(),
            "payload": {"code": "BAD_REQUEST", "message": f"Invalid HTTP method: {method}"},
        }))
        return
    
    # Allowlist & scheme check
    try:
        parsedUrl = urllib.parse.urlparse(url)
        if parsedUrl.scheme not in ("http", "https"):
            await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
                "gen": ctx.rpcConnection.gen(),
                "payload": {"code": "BAD_URL", "message": f"Unsupported scheme: {parsedUrl.scheme}"}
            }))
            return

        host = (parsedUrl.hostname or "").lower()
        if not host:
            await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
                "gen": ctx.rpcConnection.gen(),
                "payload": {"code": "BAD_URL", "message": f"Invalid URL: {url}"},
            }))
            return
        
        rawAllowList = config("httpProxy.allowList", [])
        if isinstance(rawAllowList, (list, tuple, set)):
            allowedHosts = [str(hh).lower() for hh in rawAllowList]
        else:
            # Fallback to safety if someone misconfigures to a dict/str/etc.
            allowedHosts = []
        
        if host not in allowedHosts:
            await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
                "gen": ctx.rpcConnection.gen(),
                "payload": {
                    "code": "FORBIDDEN_HOST",
                    "message": f"Host {host} not allowed",
                    "allowed": allowedHosts[:10],
                }
            }))
            return
    except Exception as err:
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcConnection.gen(),
            "payload": {"code": "BAD_URL", "message": str(err), "err": err}
        }))
        return
    
    timeoutCapMs = int(config("http.timeoutCapMs", 30_000))
    timeoutMs = min(int(msg.budgetMs if msg.budgetMs is not None else 3_000), timeoutCapMs)

    # Header policies
    requestHeadersPolicy = _pickPolicy(kind="requestHeaders", cap="http.client@1", host=host)
    requestHeaders = opts.get("headers")
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
            retries=int(config("http.retry", 2)),
            backoffBaseMs=int(config("http.backoff.baseMs", 250)),
            backoffMaxMs=int(config("http.backoff.maxMs", 1_000)),
            followRedirects=bool(opts.get("followRedirects", True)),
        )

        responseHeadersPolicy = _pickPolicy(kind="responseHeaders", cap="http.client@1", host=host)
        responseHeaders = _filterHeaders(response.get("headers"), responseHeadersPolicy)

        if not isinstance(response.get("status"), int):
            await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
                "gen": ctx.rpcConnection.gen(),
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

        await sendRPCMessage(ctx.ws, createReplyMessage(msg, {
            "gen": ctx.rpcConnection.gen(),
            "payload": payload,
        }))
        return
    except Exception as err:
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcConnection.gen(),
            "payload": {"code":"HTTP_ERROR","message":str(err),"err":err,"retryable": True},
        }))
        return



def _filterHeaders(headers: Mapping[str, Any] | None, policy: dict) -> dict[str, str]:
    mode = policy["mode"]
    listed = set(policy["list"])
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        lKey = str(key).lower()
        if(mode == "allow" and lKey in listed) or (mode == "block" and lKey not in listed):
            out[str(key)] = str(value)
    return out



def _pickPolicy(
        *,
        kind: Literal["requestHeaders","responseHeaders"],
        cap: str | None = None,
        host: str | None = None ) -> dict:
    httpProxy = config("httpProxy", {})
    base = dict(httpProxy.get(kind, {})) if isinstance(httpProxy, dict) else {} # Base policy
    policy = dict(base)

    def overlay(src: dict | None):
        nonlocal policy
        if not src:
            return
        policy = {**policy, **{key: value for key, value in src.items() if key in ("mode", "list")}}
    
    rawCap = base.get("perCap")
    perCap = rawCap if isinstance(rawCap,  dict) else {}
    rawHost = base.get("perHost")
    perHost = rawHost if isinstance(rawHost, dict) else {}

    if cap:
        overlay(perCap.get(cap))
    if host:
        overlay(perHost.get(host))

    # Normalize
    lst = policy.get("list", [])
    policy["list"] = [el.lower() for el in lst] if isinstance(lst, (list, tuple)) else []
    policy["mode"] = policy.get("mode", "block")

    return policy
