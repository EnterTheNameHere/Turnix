from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import sys

import httpx
import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.http import client as http_client


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler):
    transport = httpx.MockTransport(handler)
    original_async_client = http_client.httpx.AsyncClient

    class _PatchedAsyncClient:
        """Wrap httpx.AsyncClient so the transport can be injected."""

        def __init__(self, *args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["transport"] = transport
            kwargs["http2"] = False
            self._client = original_async_client(*args, **kwargs)

        async def __aenter__(self):
            client = await self._client.__aenter__()
            return client

        async def __aexit__(self, exc_type, exc, tb):
            return await self._client.__aexit__(exc_type, exc, tb)

    monkeypatch.setattr(http_client.httpx, "AsyncClient", _PatchedAsyncClient)
    return transport


def test_parse_retry_after_seconds():
    assert http_client._parseRetryAfter("120") == pytest.approx(120.0)


def test_parse_retry_after_http_date():
    future = datetime.now(timezone.utc) + timedelta(seconds=5)
    header = format_datetime(future)
    parsed = http_client._parseRetryAfter(header)
    assert parsed is not None
    assert parsed == pytest.approx(5.0, abs=1.5)


def test_parse_retry_after_invalid_value():
    assert http_client._parseRetryAfter("not-a-date") is None
    assert http_client._parseRetryAfter("-1") is None
    assert http_client._parseRetryAfter(None) is None


@pytest.mark.asyncio
async def test_request_retries_with_exponential_backoff(monkeypatch):
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr(http_client.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(http_client.random, "uniform", lambda _min, _max: 0)

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporary error")
        return httpx.Response(200, json={"ok": True}, headers={"Content-Type": "application/json"})

    _install_transport(monkeypatch, handler)

    result = await http_client.request(
        "GET",
        "https://example.com/resource",
        retries=1,
        backoffBaseMs=100,
        backoffMaxMs=500,
    )

    assert attempts == 2
    assert sleep_calls == [pytest.approx(0.1, abs=1e-6)]
    assert result["json"] == {"ok": True}


@pytest.mark.asyncio
async def test_request_honors_retry_after_header(monkeypatch):
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr(http_client.asyncio, "sleep", fake_sleep)

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, text="slow down", headers={"Retry-After": "1"})
        return httpx.Response(200, json={"ok": True}, headers={"Content-Type": "application/json"})

    _install_transport(monkeypatch, handler)

    result = await http_client.request(
        "POST",
        "https://example.com/things",
        retries=1,
        backoffBaseMs=100,
        backoffMaxMs=500,
    )

    assert attempts == 2
    assert sleep_calls == [pytest.approx(1.0, abs=1e-6)]
    assert result["json"] == {"ok": True}


@pytest.mark.asyncio
async def test_request_handles_invalid_json(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", headers={"Content-Type": "application/json"})

    _install_transport(monkeypatch, handler)

    result = await http_client.request("GET", "https://example.com/bad-json")

    assert result["status"] == 200
    assert result["text"] == "not-json"
    assert "json" not in result
