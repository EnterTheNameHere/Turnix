# file: first-party/observationBridge/bridge.py ; version: 2
"""Bounded selected-publication registry with a replaceable loopback HTTP adapter."""
from __future__ import annotations

import hashlib
import re
import secrets
import socket
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, RLock, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from backend.observation import ObservationGrantRevoked, ObservationReadGrant

from .schema import MAX_SAMPLES, MAX_WINDOWS, SCHEMA_ID, serialize

MAX_PUBLICATIONS = 8


def validateOrigins(origins: object) -> tuple[str, ...]:
    """Accepts only explicitly named local HTTP viewer origins with concrete ports."""
    if type(origins) not in (list, tuple) or len(origins) > 8:
        raise ValueError("allowedOrigins must be a bounded list.")
    result = []
    for origin in origins:
        if type(origin) is not str:
            raise ValueError("Invalid viewer origin.")
        parsed = urlsplit(origin)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                or parsed.port is None or not 1 <= parsed.port <= 65535 or parsed.netloc != origin[len("http://"):]):
            raise ValueError("Viewer origin must be an exact local HTTP origin with a port.")
        result.append(origin)
    return tuple(dict.fromkeys(result))


@dataclass(slots=True)
class _Publication:
    """One read claim and latest bounded response, never an accumulating history."""

    grant: ObservationReadGrant
    revision: int = 0
    fingerprint: bytes | None = None
    response: bytes | None = None


class ObservationBridge:
    """Pack-owned publication access and endpoint; never owns source sessions/collectors.

    Publication lock precedes grant/service locks. Observation revocation never
    calls back into this object. Withdrawal waits for in-flight response writers;
    source revocation prevents later grant reads, while previously copied responses
    may finish. No runtime/context callback is retained by the endpoint thread.
    """

    def __init__(self, *, port: int = 0, host: str = "127.0.0.1", allowedOrigins: object = ()) -> None:
        """Starts one explicit local endpoint, self-cleaning partial socket/thread startup."""
        if host != "127.0.0.1" or type(host) is not str:
            raise ValueError("Bridge binding must be 127.0.0.1.")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535.")
        self._origins = validateOrigins(allowedOrigins)
        self._lock = RLock()
        self._closeLock = RLock()
        self._stopping = Event()
        self._closed = False
        self._publications: dict[str, _Publication] = {}
        self._credential = secrets.token_urlsafe(32)
        self._server = None
        self._thread = None
        try:
            self._server = _Server((host, port), self)
            self._authority = f"127.0.0.1:{self._server.server_port}"
            self._thread = Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, name="actant-observation-bridge", daemon=True)
            self._thread.start()
        except BaseException:
            self.close()
            raise

    def connection(self) -> dict:
        """Returns explicit connection credentials to the same-app capability caller only."""
        with self._lock:
            if self._stopping.is_set():
                raise RuntimeError("Observation bridge is closed.")
            return {"endpoint": f"http://{self._authority}", "readCredential": self._credential,
                    "authorization": "Bearer", "schemaId": SCHEMA_ID, "allowedOrigins": list(self._origins)}

    def publish(self, grant: ObservationReadGrant) -> dict:
        """Takes ownership of a separately mediated read claim; failure revokes only that claim."""
        if type(grant) is not ObservationReadGrant:
            raise TypeError("A mediated read grant is required.")
        try:
            with self._lock:
                if self._stopping.is_set():
                    raise RuntimeError("Observation bridge is closed.")
                if len(self._publications) >= MAX_PUBLICATIONS:
                    raise ValueError("Publication capacity reached.")
                evidence = grant.snapshot()
                if evidence.request.historySamples > MAX_SAMPLES or evidence.request.historyWindows > MAX_WINDOWS:
                    raise ValueError("Source retention exceeds bridge limits.")
                publicationId = uuid4().hex
                publication = _Publication(grant)
                self._refresh(publicationId, publication, evidence)
                self._publications[publicationId] = publication
                return {"publicationId": publicationId, "snapshotUrl": f"http://{self._authority}/publications/{publicationId}",
                        "schemaId": SCHEMA_ID, "sessionId": evidence.sessionId, "clockEpochId": evidence.clockEpochId}
        except BaseException:
            grant.revoke()
            raise

    @staticmethod
    def _refresh(publicationId: str, publication: _Publication, evidence) -> None:
        """Changes revision only for changed allowlisted evidence; caches at most one response."""
        fingerprint = hashlib.sha256(serialize(evidence, publicationId, 0)).digest()
        if fingerprint != publication.fingerprint:
            response = serialize(evidence, publicationId, publication.revision + 1)
            publication.revision += 1
            publication.response = response
            publication.fingerprint = fingerprint

    def withdraw(self, publicationId: str) -> bool:
        """Stops future serving and clears cached evidence, waiting for prior sends to finish."""
        with self._lock:
            publication = self._publications.pop(publicationId, None)
            if publication is None:
                return False
            publication.response = publication.fingerprint = None
            publication.grant.revoke()
            return True

    def serve(self, publicationId: str, writer: Callable[[int, bytes], None]) -> None:
        """Revalidates every read and serializes response delivery against withdrawal/close."""
        with self._lock:
            if self._stopping.is_set():
                writer(503, b'{"error":"bridge-closed"}')
                return
            publication = self._publications.get(publicationId)
            if publication is None:
                writer(404, b'{"error":"unknown-publication"}')
                return
            try:
                evidence = publication.grant.snapshot()
                self._refresh(publicationId, publication, evidence)
            except ObservationGrantRevoked:
                publication.response = publication.fingerprint = None
                publication.grant.revoke()
                writer(410, b'{"error":"publication-revoked"}')
                return
            except Exception:
                publication.response = publication.fingerprint = None
                writer(503, b'{"error":"observation-unavailable"}')
                return
            writer(200, publication.response)

    def close(self) -> None:
        """Rejects new requests, withdraws owned readers and closes/joins endpoint resources."""
        with self._closeLock:
            self._stopping.set()
            if self._server is not None:
                self._server.interruptClients()
            with self._lock:
                if self._closed:
                    return
                for publicationId in tuple(self._publications):
                    self.withdraw(publicationId)
            try:
                if self._thread is not None and self._thread.is_alive():
                    self._server.shutdown()
                    self._thread.join(5)
                    if self._thread.is_alive():
                        raise RuntimeError("Observation endpoint did not terminate.")
            finally:
                try:
                    if self._server is not None:
                        self._server.server_close()
                finally:
                    self._credential = ""
                    self._closed = True


class _Server(HTTPServer):
    """Single request worker, finite connection timeout/backlog; no reader thread fanout."""

    request_queue_size = 8
    allow_reuse_address = False

    def __init__(self, address, bridge: ObservationBridge) -> None:
        """Binds loopback; the server retains Pack state only, never invocation context."""
        self.bridge = bridge
        self._clientsLock = RLock()
        self._clients: set[socket.socket] = set()
        super().__init__(address, _Handler)

    def get_request(self):
        """Applies a bounded socket I/O timeout before processing any HTTP headers."""
        connection, address = super().get_request()
        connection.settimeout(1.0)
        with self._clientsLock:
            if self.bridge._stopping.is_set():
                connection.close()
                raise OSError("Observation bridge is stopping.")
            self._clients.add(connection)
        return connection, address

    def interruptClients(self) -> None:
        """Unblocks partial headers and pending writes before shutdown waits for the worker."""
        with self._clientsLock:
            for connection in self._clients:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close_request(self, request) -> None:
        """Drops the accepted socket from the finite active-connection set on every exit."""
        with self._clientsLock:
            self._clients.discard(request)
        super().close_request(request)

    def handle_error(self, request, client_address) -> None:
        """Isolates broken local connections without logging requests, credentials or internals."""


class _Handler(BaseHTTPRequestHandler):
    """Strict read-only HTTP adapter with exact Host, Origin and Bearer checks."""

    server_version = "ActantObservationBridge"
    sys_version = ""

    def log_message(self, format, *args) -> None:
        """Suppresses access/error logging so headers, paths and credentials are never emitted."""

    def send_error(self, code, message=None, explain=None) -> None:
        """Keeps parser/unsupported-method errors fixed, non-reflective JSON with no-store headers."""
        self._reply(int(code), b'{"error":"http-request-rejected"}')

    def _origin(self) -> str | None:
        """Returns a validated explicit Origin, with absence allowed for authenticated local tools."""
        origins = self.headers.get_all("Origin", [])
        if not origins:
            return None
        if len(origins) != 1 or origins[0] not in self.server.bridge._origins:
            raise PermissionError("Origin rejected.")
        return origins[0]

    def _reply(self, status: int, body: bytes, origin: str | None = None, *, preflight: bool = False) -> None:
        """Writes only fixed protocol headers and a bounded allowlisted body; never reflects requests."""
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.send_header("Vary", "Origin")
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
        if preflight:
            self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Access-Control-Allow-Headers", "Authorization")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _validate(self, *, preflight: bool = False) -> tuple[str, str | None] | None:
        """Rejects ambiguous headers, URL credentials, ambient discovery and unauthorized reads."""
        bridge = self.server.bridge
        if self.headers.get_all("Host", []) != [bridge._authority]:
            self._reply(403, b'{"error":"host-rejected"}')
            return None
        try:
            origin = self._origin()
        except PermissionError:
            self._reply(403, b'{"error":"origin-rejected"}')
            return None
        match = re.fullmatch(r"/publications/([0-9a-f]{32})", self.path)
        if match is None:
            self._reply(400, b'{"error":"invalid-target"}', origin)
            return None
        if preflight:
            requested = self.headers.get_all("Access-Control-Request-Headers", [])
            if (origin is None or self.headers.get_all("Access-Control-Request-Method", []) != ["GET"]
                    or len(requested) != 1 or requested[0].strip().lower() != "authorization"):
                self._reply(403, b'{"error":"preflight-rejected"}')
                return None
        else:
            credentials = self.headers.get_all("Authorization", [])
            try:
                authorized = len(credentials) == 1 and secrets.compare_digest(credentials[0].encode("ascii"), ("Bearer " + bridge._credential).encode("ascii"))
            except UnicodeError:
                authorized = False
            if not authorized:
                self._reply(401, b'{"error":"read-credential-required"}', origin)
                return None
        return match[1], origin

    def do_GET(self) -> None:
        """Serves only a credential-authorized, explicitly selected publication."""
        validated = self._validate()
        if validated is not None:
            publicationId, origin = validated
            self.server.bridge.serve(publicationId, lambda status, body: self._reply(status, body, origin))

    def do_OPTIONS(self) -> None:
        """Answers an explicitly allowed browser preflight with no observation data."""
        validated = self._validate(preflight=True)
        if validated is not None:
            self._reply(204, b"", validated[1], preflight=True)
