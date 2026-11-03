# file: tools/executionResourceValidation/transportProbe.py ; version: 2
"""Model-free probe of the managed adapter's stop callback and HTTP buffered close.

Only an owned socket pair is used. No server executable, model, residency, domain,
or external endpoint is opened. This diagnostic cannot establish provider drain.
"""
from __future__ import annotations

import io
import json
import socket
import traceback
from http.client import HTTPResponse
from threading import Event, Thread, RLock

from backend.orchestration import CancellationSignal
from tools.executionResourceValidation.run import load_adapter


class _ObservedRaw(io.RawIOBase):
    """Expose an Event at the actual buffered reader's blocking socket read."""

    def __init__(self, channel, entered):
        """Borrow the owned channel; the probe closes both sockets separately."""
        super().__init__()
        self.channel, self.entered, self.armed = channel, entered, False

    def readable(self):
        """Permit BufferedReader to use this socket-backed byte stream."""
        return True

    def readinto(self, buffer):
        """Mark post-header reads before waiting on the owned socket."""
        if self.armed:
            self.entered.set()
        return self.channel.recv_into(buffer)


class _ProbeSocket:
    """Supply HTTPResponse its ordinary BufferedReader over observed socket IO."""

    def __init__(self, raw):
        """Retain the probe-owned byte source without connecting anywhere."""
        self.raw = raw

    def makefile(self, mode):
        """Return a real BufferedReader, preserving its normal close/read lock."""
        if mode != 'rb':
            raise ValueError('This diagnostic supports only binary HTTP reads.')
        return io.BufferedReader(self.raw)


def probe_close():
    """Observe a synchronous managed stop during a controlled blocked HTTP read.

    The peer sends no body until observation finishes. A finite socket timeout
    and finally-released peer bound diagnostic failure; neither proves quiescence.
    The 0.2-second observation is a local timing observation, not a latency SLA.
    """
    adapter = load_adapter()
    channel, peer = socket.socketpair()
    channel.settimeout(3)
    entered, stopping, stopped, read_done = Event(), Event(), Event(), Event()
    raw = _ObservedRaw(channel, entered)
    response = HTTPResponse(_ProbeSocket(raw))
    session = adapter._Session.__new__(adapter._Session)
    session.response, session.stopped = response, False
    session.stopLane = RLock()
    session.transport = adapter._transport.OwnedHttpTransport()
    session.transport._socket = channel
    channel.setblocking(False)
    session.transport.setReadTimeout(3)
    # Keep the exact Event-observed raw boundary, but let actual stop-aware
    # receive handle IO rather than blocking directly on the socket.
    def receive(buffer):
        """Mark the real raw-read boundary and use the corrected owned receive loop."""
        if raw.armed:
            entered.set()
        return session.transport.receive(buffer)

    raw.readinto = receive
    signal = CancellationSignal()
    signal.registerCallback(session.stop)
    errors = []

    def read():
        """Hold the actual HTTP buffered read until the peer releases its body."""
        try:
            response.readline()
        except Exception as error:
            errors.append({'phase': 'read', 'type': type(error).__name__,
                           'functions': [frame.name for frame in traceback.extract_tb(error.__traceback__)]})
        finally:
            response.close()  # Only the owning reader performs buffered close.
            read_done.set()

    def stop():
        """Invoke the actual signal and adapter callback on a separate thread."""
        stopping.set()
        try:
            signal.request()
        except Exception as error:
            errors.append({'phase': 'stop', 'type': type(error).__name__,
                           'functions': [frame.name for frame in traceback.extract_tb(error.__traceback__)]})
        finally:
            stopped.set()

    workers = []
    try:
        peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\n')
        response.begin()
        raw.armed = True
        reader = Thread(target=read, daemon=True)
        workers.append(reader)
        reader.start()
        if not entered.wait(2):
            raise RuntimeError('The controlled HTTP read did not enter.')
        closer = Thread(target=stop, daemon=True)
        workers.append(closer)
        closer.start()
        if not stopping.wait(2):
            raise RuntimeError('The stop worker did not enter.')
        returned_before_release = stopped.wait(0.2)
        read_done.wait(0.2)
        result = {
            'scope': 'model-free-socketpair-controlled-HTTPResponse',
            'signalRequested': signal.requested,
            'adapterStopMarked': session.stopped,
            'stopReturnedBeforePeerRelease': returned_before_release,
            'readReturnedBeforePeerRelease': read_done.is_set(),
            'providerQuiescence': 'not-tested',
        }
    finally:
        # Release only our synthetic peer; never terminate any provider/process.
        try:
            peer.sendall(b'\n')
        except OSError:
            pass
        peer.close()
        for worker in workers:
            worker.join(4)
        channel.close()
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError('Diagnostic worker did not exit within its IO guard.')
        response.close()
        session.transport.close()
    result.update(workersExited=True, transportClosed=response.isclosed(), errors=errors)
    return result


if __name__ == '__main__':
    print(json.dumps(probe_close(), indent=2))
