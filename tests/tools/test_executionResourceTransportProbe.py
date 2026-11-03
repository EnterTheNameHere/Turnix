# file: tests/tools/test_executionResourceTransportProbe.py ; version: 2
"""Exercise bounded real buffered-HTTP cancellation without any provider process."""
from tools.executionResourceValidation import transportProbe


def test_transport_probe_exits_without_provider_io(monkeypatch):
    """The actual stop callback leaves only observations and closes owned test IO."""
    adapter = transportProbe.load_adapter()

    def forbidden(*args, **kwargs):
        """Reject accidental inference/network/process startup in a model-free probe."""
        raise AssertionError('A model-free probe attempted provider IO.')

    monkeypatch.setattr(adapter.http, 'urlopen', forbidden)
    monkeypatch.setattr(adapter._impl.subprocess, 'Popen', forbidden)
    result = transportProbe.probe_close()
    assert result['scope'] == 'model-free-socketpair-controlled-HTTPResponse'
    assert result['signalRequested'] and result['adapterStopMarked']
    assert result['workersExited'] and result['transportClosed']
    assert result['providerQuiescence'] == 'not-tested'
    assert result['stopReturnedBeforePeerRelease'] and result['readReturnedBeforePeerRelease']
    # Timing and the read/close race's exception are observations, not portable
    # assertions that every scheduler or Python build must behave identically.
