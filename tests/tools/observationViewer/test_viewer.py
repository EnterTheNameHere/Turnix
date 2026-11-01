# file: tests/tools/observationViewer/test_viewer.py ; version: 1
"""Actant-suite entry point for deterministic JS tests and fixed local asset-server checks."""
from __future__ import annotations

import os
import shutil
import subprocess
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest

from tools.observationViewer.server import ViewerServer

ROOT = Path(__file__).resolve().parents[3]


def test_deterministic_viewer_javascript_contracts() -> None:
    """Runs the pure parser/model/polling/security suite under Node without browser dependencies."""
    node = os.environ.get("ACTANT_NODE") or shutil.which("node")
    assert node, "Viewer tests require Node.js 20+; set ACTANT_NODE to its executable."
    result = subprocess.run([node, "--test", str(Path(__file__).with_name("viewer.test.mjs"))], cwd=ROOT,
                            text=True, capture_output=True, check=False, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def server():
    """Starts an ephemeral fixed-asset origin and always joins its only server worker."""
    with ViewerServer(0) as service:
        worker = Thread(target=service.serve_forever, kwargs={"poll_interval": 0.02})
        worker.start()
        yield service
        service.shutdown()
        worker.join(5)
        assert not worker.is_alive()


def request(server, path: str, *, host: str | None = None):
    """Performs an asset request without any bridge credential or application data."""
    address = urlsplit(server.origin)
    connection = HTTPConnection(address.hostname, address.port, timeout=3)
    connection.request("GET", path, headers={"Host": address.netloc if host is None else host})
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


@pytest.mark.parametrize("path", ["/", "/app.mjs", "/model.mjs", "/polling.mjs", "/style.css"])
def test_fixed_assets_no_store_and_local_browser_policy(server, path):
    """The launcher serves only public viewer assets under a restrictive local policy."""
    status, headers, body = request(server, path)
    assert status == 200 and body
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "connect-src http://127.0.0.1:*" in headers["Content-Security-Policy"]
    assert "form-action 'none'" in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize("path", ["/../backend/observation.py", "/README.md", "/?credential=private-sentinel", "/publications", "/favicon.ico"])
def test_no_directory_proxy_or_reflected_queries(server, path, capsys):
    """Unknown paths cannot turn the static viewer into a proxy, file server or credential log."""
    status, _headers, body = request(server, path)
    assert status == 404 and b"private-sentinel" not in body
    captured = capsys.readouterr()
    assert "private-sentinel" not in captured.out + captured.err


def test_viewer_rejects_nonlocal_host(server):
    """A browser reaching loopback through a foreign Host receives no viewer asset."""
    status, _headers, _body = request(server, "/", host="attacker.example")
    assert status == 403
