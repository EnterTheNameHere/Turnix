# file: tests/tools/test_executionResourceAbortProbe.py ; version: 3
"""Protect the explicit exact-handle emergency boundary; no real provider is launched."""
import subprocess
from threading import Event
from types import SimpleNamespace

import pytest

from tools.executionResourceValidation.abortProbe import OwnedChild, Probe, dispose, exception_facts


class Child:
    """Controlled Popen substitute separating attempts from actual exit/reaping."""

    def __init__(self):
        """Expose a stable Windows-like handle and initially live process."""
        self.pid, self._handle, self.returncode = 101, 77, None
        self.calls, self.timeout = [], False

    def poll(self):
        """Report only independently controlled child exit status."""
        return self.returncode

    def terminate(self):
        """Record an attempt without claiming actual exit."""
        self.calls.append('terminate')

    def kill(self):
        """Record only an explicitly requested escalation."""
        self.calls.append('kill')

    def wait(self, timeout):
        """Either confirm/reap or leave actual child disposition unknown."""
        self.calls.append('wait')
        if self.timeout:
            raise subprocess.TimeoutExpired('owned-test-child', timeout)
        self.returncode = 0
        return 0


@pytest.mark.parametrize('drift', ['pid', 'handle', 'object', 'extra-child'])
def test_disposal_rejects_identity_drift(drift):
    """A remembered PID cannot authorize control of a replacement/ambiguous child."""
    process = Child()
    child = OwnedChild.capture(process)
    processes = [process]
    if drift == 'pid':
        process.pid += 1
    elif drift == 'handle':
        process._handle += 1
    elif drift == 'object':
        processes = [Child()]
    else:
        processes.append(Child())
    with pytest.raises(RuntimeError, match='identity'):
        dispose(child, processes, 'terminate', [])
    assert process.calls == []


def test_terminate_timeout_never_automatically_kills_or_claims_exit():
    """One explicit attempt can leave a live child, requiring a second operator command."""
    process = Child()
    process.timeout = True
    child = OwnedChild.capture(process)
    result = dispose(child, [process], 'terminate', [])
    assert result['waitTimedOut'] and result['reaped'] is False
    assert 'exitCode' not in result and process.returncode is None
    assert process.calls == ['terminate', 'wait']


def test_kill_requires_separate_failed_terminate_evidence():
    """A kill command cannot become the first or an unrecorded disposal step."""
    process = Child()
    child = OwnedChild.capture(process)
    with pytest.raises(RuntimeError, match='separately observed'):
        dispose(child, [process], 'kill', [])
    assert process.calls == []
    result = dispose(child, [process], 'kill', [{'action': 'terminate', 'waitTimedOut': True}])
    assert result['reaped'] and result['exitCode'] == 0
    assert process.calls == ['kill', 'wait']


def test_existing_exit_is_reaped_without_another_termination_attempt():
    """An already exited exact child is confirmed without signaling any PID."""
    process = Child()
    child = OwnedChild.capture(process)
    process.returncode = 0
    result = dispose(child, [process], 'terminate', [])
    assert result['alreadyExited'] and result['reaped']
    assert process.calls == ['wait']


@pytest.mark.parametrize('slot', [{'id': 0, 'is_processing': False, 'id_task': 1},
    {'id': 0, 'is_processing': True}, {'id': 0, 'is_processing': True, 'id_task': True}])
def test_inadequate_active_binding_does_not_trigger_an_abort(slot):
    """Idle previous IDs, absent IDs and boolean IDs cannot target live cancellation."""
    probe = Probe.__new__(Probe)
    probe.slots = lambda: slot
    records = []
    probe.record = lambda kind, **values: records.append(kind)
    probe.at_boundary(type('Session', (), {'nativeDone': False})())
    assert records == ['inconclusive-before-active-capture']


def test_suppressed_transport_timeout_is_retained_as_private_evidence():
    """Provider normalization from None must not hide the actual socket failure type."""
    try:
        try:
            raise TimeoutError('test-only controlled socket timeout')
        except TimeoutError:
            raise RuntimeError('normalized provider error') from None
    except RuntimeError as error:
        facts = exception_facts(error)
    assert [item['type'] for item in facts] == ['RuntimeError', 'TimeoutError']


def test_exception_context_cycle_is_bounded():
    """Malformed exception chains cannot turn evidence recording into a hung worker."""
    error = RuntimeError('test-only cycle')
    error.__context__ = error
    assert len(exception_facts(error)) == 1


def test_pre_header_probe_waits_past_initial_idle_without_fabricating_binding():
    """A bounded initial idle sample is followed by actual active capture before abort."""
    probe = Probe.__new__(Probe)
    probe.case = 'pre-header-cancel'
    probe.bound = Event()
    probe.bound.set()
    probe.session = SimpleNamespace(response=None)
    replies = iter([{'id': 0, 'is_processing': False},
                    {'id': 0, 'is_processing': True, 'id_task': 4}])
    probe.slots = lambda: next(replies)
    records, calls = [], []
    probe.record = lambda kind, **values: records.append((kind, values))
    probe.launch_worker = lambda target: calls.append('monitor')
    probe.job = SimpleNamespace(requestCancellation=lambda: calls.append('cancel'))
    probe.pre_header_cancel()
    assert probe.captured and probe.native_task == 4 and probe.abort_requested
    assert calls == ['monitor', 'cancel']
    assert next(values for kind, values in records if kind == 'abort-requested')['headersReturned'] is False
