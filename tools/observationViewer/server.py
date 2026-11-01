# file: tools/observationViewer/server.py ; version: 1
"""Fixed-asset local viewer launcher; imports no Actant runtime and proxies no observations."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          **{"/" + name: (name, "text/javascript; charset=utf-8") for name in ("app.mjs", "model.mjs", "polling.mjs")}}


class ViewerServer(HTTPServer):
    """Serves only the viewer's fixed public assets on one explicit IPv4 loopback origin."""

    def __init__(self, port: int = 7777) -> None:
        """Binds a local port; test code can use zero before configuring the bridge Origin."""
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid local viewer port.")
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def get_request(self):
        """Bounds local asset-header reads so one idle client cannot stall the launcher forever."""
        connection, address = super().get_request()
        connection.settimeout(1)
        return connection, address


class Handler(BaseHTTPRequestHandler):
    """No directory listing, path traversal, query reflection, data proxy or access logging."""

    def log_message(self, format, *args) -> None:
        """Keeps request paths and any accidentally submitted credentials out of logs."""

    def send_error(self, code, message=None, explain=None) -> None:
        """Returns fixed errors without reflecting request syntax or paths."""
        self.reply(int(code), b"Request rejected.", "text/plain; charset=utf-8")

    def reply(self, status: int, body: bytes, contentType: str) -> None:
        """Applies non-persistent asset and restrictive local browser security headers."""
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", contentType)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src http://127.0.0.1:*; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Serves an exact known asset only, validating the literal local Host."""
        if self.headers.get_all("Host", []) != [urlsplit(self.server.origin).netloc]:
            self.reply(403, b"Host rejected.", "text/plain")
            return
        if self.path not in ASSETS:
            self.reply(404, b"Unknown viewer asset.", "text/plain")
            return
        name, contentType = ASSETS[self.path]
        self.reply(200, (ROOT / name).read_bytes(), contentType)


def main() -> None:
    """Launches only static viewer assets at an explicit origin for prior bridge configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7777)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Choose an explicit port between 1 and 65535.")
    with ViewerServer(args.port) as server:
        print(f"Viewer: {server.origin}\nConfigure this exact origin in the bridge before activation. Ctrl+C stops only the viewer server.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
