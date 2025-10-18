# mods/first-party/drivers/llamacpp/llamacpp_client.py

from __future__ import annotations
import asyncio
import httpx, json, math, secrets
from typing import AsyncGenerator, Any

from backend.core.logger import getModLogger
logger = getModLogger("llamacpp_client")

DEFAULT_BASE_URL = "http://localhost:1234"
# TODO: check for valid paths / returned formats - do it on init? Check streamChat TODOs too...
TOKENIZE_PATH = "/tokenize"
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

_MUST_NOT_OVERRIDE: set[str] = {"messages", "model", "stream", "temperature", "max_tokens"}
_KNOWN_EXTRAS: set[str] = {
    "stop", "presence_penalty", "frequency_penalty", "top_p", "seed",
    "mirostat", "mirostat_tau", "mirostat_eta", "repeat_penalty",
    "repeat_last_n", "typical_p", "min_p",
}

def _mergeExtraSafely(payload: dict[str, Any], extra: dict[str, Any] | None, *, strict: bool = False) -> None:
    if not extra or not extra.get("allow_extra_params"):
        return
    ex = dict(extra) # Shallow copy, so we don't mutate caller's dict
    ex.pop("allow_extra_params", None)

    for key, value in ex.items():
        if value is None: # Skip nulls
            continue
        if key in _MUST_NOT_OVERRIDE:
            logger.debug("extra ignored (core field): %s", key)
            continue
        if strict and key not in _KNOWN_EXTRAS:
            logger.debug("extra ignored (unknown in strict mode): %s", key)
            continue
        if key not in _KNOWN_EXTRAS:
            logger.debug("extra passthrough (unknown): %s", key)
        payload[key] = value
    
    payload["stream"] = True # Enforced, cannot be overridden

def _safeFloat(value: Any, low: float, high: float, *, default: float) -> float:
    try:
        val = float(value)
        if not math.isfinite(val):
            return default
        return min(high, max(low, val))
    except Exception:
        return default

def _safeInt(value: Any, low: int, high: int, *, default: int) -> int:
    try:
        val = float(value)
        if not math.isfinite(val):
            return default
        return max(low, min(high, int(val)))
    except Exception:
        return default

class LlamaCppClient:
    def __init__(self, baseUrl: str = DEFAULT_BASE_URL, timeoutS: int = 300):
        self.baseUrl = baseUrl.rstrip("/")
        self.timeoutS = timeoutS
        self.client = httpx.AsyncClient(
            base_url=self.baseUrl,
            timeout=httpx.Timeout(connect=self.timeoutS, read=None, write=self.timeoutS, pool=None),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    # TODO: Validate samplers values and protect against NaN/Inf
    # TODO: Maybe try which samplers are supported by checking if their usage makes server respond with error - do it at initiation?
    # TODO: Use grammar so prompt use is correct like if system role is ignored...
    async def streamChat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        temperature: float = 0.8,
        max_tokens: int = 512,
        *,
        top_p: float | None = 1.0,
        extra: dict[str, Any] | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Streams OpenAI-compatible chat completions from llama.cpp.
        Yields OpenAI-style events, e.g.:
            {"choices":[{"delta":{"content":"..."}, "index":0, "finish_reason":None}]}
        and finally yields
            {"done":True}
        when the server sends [DONE]

        Cooperative cancellation: if the caller cancels the consuming task,
        we ensure the HTTP stream is closed cleanly.
        """
        requestId = f"req_{id(self):x}_{int(asyncio.get_running_loop().time()*1e6):x}_{secrets.token_hex(3)}"
        if not isinstance(messages, list) or not messages:
            yield {
                "error": "No messages provided",
                "requestId": requestId,
            }
            return
       
        payload: dict[str, Any] = {
            "model": model,
            "temperature": _safeFloat(temperature, 0.0, 2.0, default=0.8),
            "max_tokens": _safeInt(max_tokens, 1, 32768,  default=512), # TODO: make max to be n_ctx
            "stream": True,
        }
        
        normalized_msgs = []
        for msg in messages:
            roleIn = str(msg.get("role") or "user").lower()
            # TODO: check if other roles like "tool" is supported
            role = roleIn if roleIn in {"system", "user", "assistant"} else "user"
            content = "" if msg.get("content") is None else str(msg.get("content"))
            normalized_msgs.append({"role": role, "content": content})
        payload["messages"] = normalized_msgs
        
        if top_p is not None:
            payload["top_p"] = _safeFloat(top_p, 0.0, 1.0, default=1.0)
        
        _mergeExtraSafely(payload=payload, extra=extra, strict=False)
        
        url = CHAT_COMPLETIONS_PATH
        # TODO: Prevent leaking of private data like API key
        logger.debug("(%s) Sending payload to LLM: %s", requestId, payload)
        
        respCM = self.client.stream("POST", url, json=payload, headers={"Accept": "text/event-stream"})
        resp: httpx.Response | None = None
        
        try:
            gotDone = False
            async with respCM as r:
                resp = r

                # Raise early on non-2xx
                try:
                    r.raise_for_status()
                except httpx.HTTPStatusError as httpErr:
                    text = await r.aread()
                    retryAfter = r.headers.get("retry-after")
                    yield {
                        "error": "HTTP status error",
                        "status": r.status_code,
                        "body": text.decode(errors="replace"),
                        "exception": repr(httpErr),
                        "requestId": requestId,
                        "retryAfter": retryAfter,
                    }
                    return

                buf: list[str] = [] # Accumulate "data:" lines for one SSE event
                async for raw in r.aiter_lines():
                    # Graceful task cancel support
                    if (task := asyncio.current_task()) is not None and task.cancelled():
                        raise asyncio.CancelledError()

                    if raw is None:
                        continue
                    line = raw.strip()

                    # Blank line => send accumulated event data
                    if not line:
                        if not buf:
                            continue
                        data = "\n".join(buf)
                        buf.clear()
                        if data.strip() == "[DONE]":
                            yield {
                                "done": True,
                                "requestId": requestId
                            }
                            gotDone = True
                            break
                        try:
                            obj = json.loads(data)
                        except Exception as parseErr:
                            logger.warning("(%s) Received bad chunk: %r", requestId, data)
                            yield {
                                "error": "Malformed JSON chunk",
                                "raw": data,
                                "exception": repr(parseErr),
                                "requestId": requestId
                            }
                            continue
                        if "choices" not in obj and "content" in obj:
                            obj: dict = {"choices": [{"delta": {"content": obj["content"]}, "index": 0, "finish_reason": None}]}
                        obj["raw"] = data
                        obj["requestId"] = requestId
                        yield obj
                        continue

                    if line.startswith((":", "event:", "id:", "retry:")):
                        continue # comment/keepalive/ignored event types
                    if line.startswith("data:"):
                        buf.append(line[5:].lstrip())
                        continue

                    # Unexpected line
                    yield {
                        "error": "Unrecognized SSE line",
                        "requestId": requestId,
                        "raw": line,
                    }

                # Flush pending event if stream ended without trailing blank line
                if not gotDone and buf:
                    data = "\n".join(buf)
                    buf.clear()
                    if data.strip() == "[DONE]":
                        yield {
                            "done": True,
                            "requestId": requestId,
                        }
                        gotDone = True
                    else:
                        try:
                            obj = json.loads(data)
                        except Exception as parseErr:
                            logger.warning("(%s) Received bad chunk at EOF: %r", requestId, data)
                            yield {
                                "error": "Malformed JSON chunk",
                                "raw": data,
                                "exception": repr(parseErr),
                                "requestId": requestId,
                            }
                        else:
                            if "choices" not in obj and "content" in obj:
                                obj = {"choices": [{"delta": {"content": obj["content"]}, "index": 0, "finish_reason": None}]}
                            obj["raw"] = data
                            obj["requestId"] = requestId
                            yield obj
                    
                # In case somehow we ended and it was not with [DONE]
                if not gotDone:
                    yield {
                        "done": True,
                        "requestId": requestId,
                        "eof": True
                    }

        except asyncio.CancelledError:
            # Cooperative cancel: close stream (if open) and exit quietly
            if resp is not None:
                try:
                    await resp.aclose()
                except Exception:
                    pass
            raise
        except httpx.RequestError as reqErr:
            yield {
                "error": "HTTP request error",
                "exception": repr(reqErr),
                "requestId": requestId,
            }
        except Exception as err:
            logger.exception("(%s) Unexpected error in streamChat", requestId)
            yield {
                "error": "Unexpected error",
                "exception": repr(err),
                "requestId": requestId,
            }
    
    # Simple token count helper (non-streaming), if server supports it
    # TODO: add "add_special", "parse_special" or "with_pieces"
    async def countTokens(self, text: str) -> int | None:
        try:
            resp = await self.client.post(TOKENIZE_PATH, json={"content": text})
            resp.raise_for_status()
            obj = resp.json()
            tokens = obj.get("tokens")
            return len(tokens) if isinstance(tokens, list) else None
        except Exception as err:
            logger.debug("Token count failed: %r", err)
            return None

async def onLoad(ctx):
    """
    Called by the backend mod loader.
    Return the driver instance; it will be registered under a name.
    """

    # TODO: Read cxt.settings to support customization
    client = LlamaCppClient()
    ctx.registerService("llm", client)
    return {"ok": True}
