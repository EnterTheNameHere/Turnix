from __future__ import annotations
import asyncio, random, httpx
from typing import Any
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

class HTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body

def _parseRetryAfter(value: str | None) -> float | None:
    """Return seconds suggested by Retry-After header, if parsable."""
    if not value:
        return None
    # Retry-After: seconds
    try:
        secondsF = float(value)
        if secondsF >= 0:
            return secondsF
    except ValueError:
        pass
    # Retry-After: HTTP-date
    try:
        dt = parsedate_to_datetime(value)
        # Normalize to aware UTC for safe subtraction
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc).timestamp()
        return max(0.0, dt.timestamp() - now)
    except Exception:
        return None

def _shouldRetry(status: int) -> bool:
    # Typical transient HTTP errors upon which retry makes sense
    return status in (408, 429, 500, 502, 503, 504)

async def request(
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        data: Any | None = None,
        params: dict[str, Any] | None = None,
        timeoutMs: int = 30_000,
        retries: int = 2,
        backoffBaseMs: int = 250,
        backoffMaxMs: int = 1_000,
        followRedirects: bool = True) -> dict[str, Any]:
    """
    Simple outbound HTTP client with timeout and retries (408/429/5xx).
    Returns: {"status": int, "headers": dict[str,str], "text": str, "content": bytes, "json": Any?}
    - When the response is JSON, a best-effort parsed value is included under "json".
    - Raises HTTPError for 408/429/5xx after exhausting retries (or immediately for non-retryable 5xx).
    - Raises RuntimeError for non-HTTP transport errors after exhausting retries.
    """
    # Separate connect/read/write/pool timeouts can be useful; keep single total here.
    if timeoutMs <= 0:
        timeoutMs = 1
    timeout = httpx.Timeout(timeoutMs / 1_000)
    attempt = 0
    method = str(method).upper()
    retries = max(0, retries)
    if json is not None and data is not None:
        raise ValueError("Pass either 'json' or 'data', not both")

    async with httpx.AsyncClient(timeout=timeout, http2=True) as cli:
        while True:
            try:
                resp = await cli.request(method, url, headers=headers, json=json, data=data, params=params, follow_redirects=followRedirects)
                status = resp.status_code
                
                # Retry policy
                if _shouldRetry(status) and attempt < retries:
                    retryAfter = _parseRetryAfter(resp.headers.get("Retry-After"))
                    delay: float
                    if retryAfter is not None:
                        delay = retryAfter
                    else:
                        # Exponential backoff with jitter
                        base = min(backoffMaxMs, backoffBaseMs * (2 ** attempt))
                        jitter = base * 0.25
                        delayMs = max(0, base + random.uniform(-jitter, jitter))
                        delay = delayMs / 1000.0
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                
                # For other 5xx (non-retry or retries exhausted) raise
                if status >= 500 or status in (408,429):
                    raise HTTPError(status, resp.text)
                
                # Success or non-retryable 4xx: return payload (no exception)
                out = {
                    "status": status,
                    "headers": dict(resp.headers), # note: Duplicate header keys are collapsed
                    "text": resp.text,
                    "content": resp.content,
                }
                # Best-effort JSON parse
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype.lower():
                    try:
                        out["json"] = resp.json()
                    except Exception:
                        # Keep going; caller still has "text"
                        pass
                return out

            except asyncio.CancelledError:
                # Bubble up cancellation
                raise
            except HTTPError as err:
                attempt += 1
                if attempt > retries:
                    # Exhausted retries for HTTPError
                    raise
                # Backoff before next attempt
                base = min(backoffMaxMs, backoffBaseMs * (2 ** (attempt - 1)))
                jitter = base * 0.25
                delayMs = max(0, base + random.uniform(-jitter, jitter))
                await asyncio.sleep(delayMs / 1000.0)
            except httpx.HTTPError as err:
                attempt += 1
                if attempt > retries:
                    # Transport-level error after retries
                    raise RuntimeError(str(err)) from err
                base = min(backoffMaxMs, backoffBaseMs * (2 ** (attempt - 1)))
                jitter = base * 0.25
                delayMs = max(0, base + random.uniform(-jitter, jitter))
                await asyncio.sleep(delayMs / 1000.0)
