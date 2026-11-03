# file: tests/first_party/llmDrivers/test_llamaCppOwnedTransport.py ; version: 1
"""Real loopback transport interruption; no executable, model or provider claim is used."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread, get_ident
from urllib.request import Request

import pytest

from tests.first_party.llmDrivers.test_llamaCppExecutionResources import adapter


@pytest.mark.parametrize('phase', ['pre-header', 'blocked-read'])
def test_stop_interrupts_real_io_without_closing_buffer_on_the_signal_thread(phase):
    """A socket stop returns while the server withholds data; only the reader closes response."""
    entered, release, reading = Event(), Event(), Event()
    closed_by = []

    class Handler(BaseHTTPRequestHandler):
        """Hold either headers or body on this test-owned loopback connection."""

        def do_POST(self):
            """Accept the exact body, then withhold the selected HTTP phase."""
            self.rfile.read(int(self.headers['Content-Length']))
            try:
                if phase == 'pre-header':
                    entered.set()
                    release.wait(3)
                self.send_response(200)
                self.send_header('Content-Length', '100')
                self.end_headers()
                self.wfile.flush()
                if phase == 'blocked-read':
                    entered.set()
                    release.wait(3)
                self.wfile.write(b'\n')
                self.wfile.flush()
            except OSError:
                pass  # Expected client socket shutdown, not a hidden test failure.

        def log_message(self, *args):
            """Do not emit request contents or create an external log sink."""

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    worker = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    worker.start()
    transport = adapter._transport.OwnedHttpTransport()
    request = Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions', data=b'{}')
    reader_identity = []

    def read():
        """Own response finalization even when the stop thread interrupts headers/body."""
        reader_identity.append(get_ident())
        try:
            response = transport.open(request, timeout=2)
            close = response.close

            def close_response():
                """Observe which thread performs the real buffered close."""
                closed_by.append(get_ident())
                close()

            response.close = close_response
            reading.set()
            return response.readline()
        except OSError:
            return 'interrupted'
        finally:
            transport.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(read)
            try:
                assert entered.wait(2)
                if phase == 'blocked-read':
                    assert reading.wait(2)
                pool.submit(transport.stop).result(1)
                pool.submit(transport.stop).result(1)  # Idempotent, no new connection.
                pending.result(2)
                assert not release.is_set()
                assert transport.mayHaveSent
                assert all(identity == reader_identity[0] for identity in closed_by)
                if phase == 'blocked-read':
                    assert closed_by
            finally:
                release.set()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_stop_before_open_prevents_address_resolution_or_transmission(monkeypatch):
    """A pre-stopped operation cannot allocate or reconnect through an alternate address."""
    transport = adapter._transport.OwnedHttpTransport()

    def forbidden(*args, **kwargs):
        """Any connect work after an inert stop is a test failure."""
        raise AssertionError('Unexpected address resolution.')

    monkeypatch.setattr(adapter._transport.socket, 'getaddrinfo', forbidden)
    transport.stop()
    with pytest.raises(InterruptedError):
        transport.open(Request('http://127.0.0.1:1/stream', data=b'{}'), timeout=1)
    assert not transport.mayHaveSent
    transport.close()


def test_stop_during_connect_prevents_body_send_and_alternate_connection(monkeypatch):
    """The socket is published before connect, so a late successful connect cannot send."""
    transport = adapter._transport.OwnedHttpTransport()
    entered, release = Event(), Event()
    calls = []

    class Socket:
        """Controlled raw socket whose connect outlives a stop request."""

        def setblocking(self, blocking):
            """Record no IO for this controlled nonblocking connection."""

        def connect_ex(self, address):
            """Retain connect until stop has reached this exact raw object."""
            entered.set()
            assert release.wait(3)
            return 0

        def shutdown(self, how):
            """Record the actual raw stop mechanism without a buffered reader."""
            calls.append('shutdown')

        def close(self):
            """Record raw close; it cannot open another connection."""
            calls.append('close')

    monkeypatch.setattr(adapter._transport.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 1))] * 2)
    monkeypatch.setattr(adapter._transport.socket, 'socket', lambda *a, **k: Socket())

    def open_request():
        """Own finalization of the interrupted partial-open operation."""
        try:
            return transport.open(Request('http://127.0.0.1:1/stream', data=b'{}'), timeout=1)
        finally:
            transport.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(open_request)
        assert entered.wait(2)
        try:
            transport.stop()
            assert calls[0] == 'shutdown'
        finally:
            release.set()
        with pytest.raises(InterruptedError):
            pending.result(2)
    assert not transport.mayHaveSent
