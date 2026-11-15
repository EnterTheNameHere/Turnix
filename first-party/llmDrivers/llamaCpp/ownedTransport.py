# file: first-party/llmDrivers/llamaCpp/ownedTransport.py ; version: 1
"""Single-operation managed HTTP transport with socket-only stop delivery.

No proxy, redirect, connection reuse, process control or quiescence assertion is
provided. The reader owns response/connection finalization; stop never acquires
the HTTPResponse/BufferedReader lock. This is private llama.cpp adapter substrate.
"""
from __future__ import annotations

import socket
import errno
import io
import select
import time
from http.client import HTTPConnection
from threading import RLock
from urllib.parse import urlsplit


class _Connection(HTTPConnection):
    """Publish each raw socket to its transport before a blocking connect."""

    def __init__(self, host, port, timeout, transport):
        """Bind only one explicit managed endpoint and its operation owner."""
        super().__init__(host, port, timeout=timeout)
        self.transport = transport

    def connect(self):
        """Connect through the stop-aware owner; no tunnelling or implicit proxy."""
        self.sock = self.transport.connect(self.host, self.port, self.timeout)


class _RawReader(io.RawIOBase):
    """Feed HTTPResponse through stop-aware raw reads without poisoning on poll timeouts."""

    def __init__(self, transport):
        """Borrow the transport socket; final descriptor close remains transport-owned."""
        super().__init__()
        self.transport = transport

    def readable(self):
        """Allow ordinary HTTPResponse buffering over the owned receive loop."""
        return True

    def readinto(self, buffer):
        """Receive once after bounded stop-aware waits; return zero only for actual EOF."""
        return self.transport.receive(buffer)


class _HttpSocket:
    """Single-request HTTPConnection adapter; raw socket lifetime stays with its owner."""

    def __init__(self, transport):
        """Bind one operation's explicit socket IO and buffered-reader factory."""
        self.transport = transport

    def sendall(self, data):
        """Send unchanged HTTP bytes through bounded stop-aware writes."""
        self.transport.send(data)

    def makefile(self, mode):
        """Create the reader-owned standard buffer over this operation's raw reads."""
        if mode != "rb":
            raise ValueError("Managed inference supports only binary HTTP reads.")
        return io.BufferedReader(_RawReader(self.transport))

    def close(self):
        """HTTPConnection relinquishes its reference; transport owns descriptor disposal.

        HTTPConnection may close its socket reference immediately after receiving
        Connection: close headers, while HTTPResponse still needs the body.
        """


class OwnedHttpTransport:
    """Own a raw socket through pre-header open, stream reading and reader close."""

    def __init__(self):
        """Create an inert single-use transport with no IO or application authority."""
        self._lane = RLock()
        self._socket = self._connection = self.response = None
        self._stopped = self._opened = self._closed = False
        self.mayHaveSent = False
        self.readTimeout = None

    def requireRunning(self):
        """Reject new/raw IO once a stop or reader final close has been recorded."""
        with self._lane:
            if self._stopped or self._closed:
                raise InterruptedError("Managed transport has stopped.")

    def waitSocket(self, channel, deadline, *, writing=False):
        """Wait in short slices; Windows shutdown alone need not wake timeout-mode reads."""
        while True:
            self.requireRunning()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Managed HTTP operation timed out.")
            ready, writable, exceptional = select.select(
                [] if writing else [channel], [channel] if writing else [],
                [channel], min(remaining, 0.05))
            self.requireRunning()
            if ready or writable or exceptional:
                return

    def receive(self, buffer):
        """Receive body/header bytes with a true per-read timeout and independent stop check."""
        channel = self._socket
        deadline = time.monotonic() + self.readTimeout
        while True:
            self.requireRunning()
            try:
                return channel.recv_into(buffer)
            except BlockingIOError:
                self.waitSocket(channel, deadline)

    def send(self, data):
        """Transmit each supplied HTTP byte once; partial failure stays possibly sent."""
        channel = self._socket
        deadline = time.monotonic() + self.readTimeout
        remaining = memoryview(data)
        while remaining:
            self.requireRunning()
            try:
                count = channel.send(remaining)
                if count <= 0:
                    raise OSError("Managed HTTP connection closed during send.")
                remaining = remaining[count:]
            except BlockingIOError:
                self.waitSocket(channel, deadline, writing=True)

    def setReadTimeout(self, seconds):
        """Set a private transport read bound, used by supervised timeout probes."""
        import math
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Managed read timeout must be positive and finite.")
        self.readTimeout = float(seconds)

    def connect(self, host, port, timeout):
        """Publish before connect so cancellation cannot miss an allocated socket.

        Address resolution itself remains subject to OS resolver behavior. A stop
        observed after resolution prevents all subsequent connection/send work.
        """
        last = None
        resolver_host = host.encode("ascii") if host.isascii() else host
        for family, kind, protocol, _, address in socket.getaddrinfo(resolver_host, port, type=socket.SOCK_STREAM):
            with self._lane:
                if self._stopped or self._closed:
                    raise InterruptedError("Managed transport stopped before connect.")
                channel = socket.socket(family, kind, protocol)
                channel.setblocking(False)
                self._socket = channel
            try:
                result = channel.connect_ex(address)
                pending = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035, 10036, 10037}
                if result in pending:
                    self.waitSocket(channel, time.monotonic() + timeout, writing=True)
                    result = channel.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if result:
                    raise OSError(result, "Managed endpoint connection failed.")
                self.requireRunning()
                return _HttpSocket(self)
            except OSError as error:
                channel.close()
                last = error
                with self._lane:
                    if self._stopped or self._closed:
                        raise InterruptedError("Managed transport stopped during connect.") from None
        if last is not None:
            raise last
        raise OSError("Managed endpoint has no usable address.")

    def open(self, request, *, timeout):
        """Send one request and obtain headers; a stop can interrupt either phase."""
        endpoint = urlsplit(request.full_url)
        if endpoint.scheme != "http" or not endpoint.hostname or endpoint.username or endpoint.fragment:
            raise ValueError("Managed inference requires an explicit plain HTTP endpoint.")
        with self._lane:
            if self._opened or self._stopped or self._closed:
                raise InterruptedError("Managed transport is stopped or already used.")
            self._opened = True
            self.setReadTimeout(timeout)
            connection = _Connection(endpoint.hostname, endpoint.port or 80, timeout, self)
            self._connection = connection
        connection.connect()
        with self._lane:
            if self._stopped or self._closed:
                raise InterruptedError("Managed transport stopped before send.")
            self.mayHaveSent = True  # Sending can fail after partial server acceptance.
        path = endpoint.path or "/"
        if endpoint.query:
            path += "?" + endpoint.query
        connection.request(request.get_method(), path, body=request.data,
                           headers={**dict(request.header_items()), "Connection": "close"})
        response = connection.getresponse()
        self.response = response
        with self._lane:
            if self._stopped:
                raise InterruptedError("Managed transport stopped before headers returned.")
        if response.status != 200:
            raise OSError("Managed inference returned an unsuccessful HTTP status.")
        return response

    def stop(self):
        """Interrupt only this raw socket, leaving buffered final close to the reader."""
        with self._lane:
            if self._stopped:
                return
            self._stopped = True
            channel = self._socket
        if channel is not None:
            try:
                channel.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # Unconnected/already closed is not provider-stop evidence.
            try:
                channel.close()
            except OSError:
                pass

    def close(self):
        """Finalize once on the owning reader thread, including partial-open failures."""
        with self._lane:
            if self._closed:
                return
            self._closed = True
        try:
            if self.response is not None:
                self.response.close()
        finally:
            if self._connection is not None:
                self._connection.close()
            if self._socket is not None:
                self._socket.close()
