# file: tools/executionResourceValidation/abortProbe.py ; version: 3
"""Interactive, one-case ER-04C probe; emergency disposal requires explicit commands.

Approval: canonical actant-integration coordination commit 2fff776, ER-04C
supervised-exact-child-cleanup authorization (recipient inbox/archive).
No automatic process termination, restart, claim repair, or domain reuse occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from threading import Event, RLock, Thread
from urllib.parse import urlencode
from uuid import uuid4

from backend.orchestration import Job
from tools.executionResourceValidation.preflight import load_config
from tools.executionResourceValidation.run import (
    ROOT, Recorder, Validation, load_adapter, plain, transport_facts, unused_port, write_json,
)

CASES = ('job-cancel', 'disconnect', 'early-close', 'read-timeout', 'stream-delete',
         'blocked-job-cancel', 'pre-header-cancel')


def exception_facts(error):
    """Retain suppressed transport cause types privately without changing error semantics."""
    result, seen = [], set()
    while error is not None and id(error) not in seen and len(result) < 8:
        seen.add(id(error))
        result.append({'type': type(error).__name__,
                       'functions': [frame.name for frame in traceback.extract_tb(error.__traceback__)]})
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return result


@dataclass(frozen=True)
class OwnedChild:
    """Retain the original Windows Popen object and kernel handle, never reopen a PID."""

    process: object
    pid: int
    handle: int

    @classmethod
    def capture(cls, process):
        """Require a live exact Windows child handle before admitting an abort."""
        if process.poll() is not None or not hasattr(process, '_handle'):
            raise RuntimeError('A live retained Windows child handle is required.')
        return cls(process, process.pid, int(process._handle))

    def require(self, processes):
        """Reject identity drift or extra children without issuing a process operation."""
        if (len(processes) != 1 or processes[0] is not self.process or
                self.process.pid != self.pid or int(self.process._handle) != self.handle):
            raise RuntimeError('The original sole child identity is no longer established.')


def dispose(child, processes, action, previous, wait_seconds=3):
    """Perform one explicit exact-handle disposal step; never escalate automatically.

    kill requires a prior terminate wait-timeout. Exit is recorded only after
    Popen.wait succeeds. This function conveys no Actant claim/recovery authority.
    """
    child.require(processes)
    process = child.process
    if action not in ('terminate', 'kill'):
        raise ValueError('Unknown disposal action.')
    if action == 'kill' and not any(item.get('action') == 'terminate' and item.get('waitTimedOut') for item in previous):
        raise RuntimeError('kill requires a separately observed failed terminate wait.')
    record = {'action': action, 'requestedAt': time.monotonic(), 'alreadyExited': process.poll() is not None}
    if not record['alreadyExited']:
        getattr(process, action)()
    try:
        record['exitCode'] = process.wait(timeout=wait_seconds)
        record['reaped'] = True
    except subprocess.TimeoutExpired:
        record.update(reaped=False, waitTimedOut=True)
    return record


class Probe:
    """Keep all process/session authority rooted while stdin remains independently usable."""

    def __init__(self, config, folder, case):
        """Create one isolated trusted host and a test-only admitted stream wrapper."""
        self.config, self.folder, self.case = config, folder, case
        self.token, self.conv_id = uuid4().hex, uuid4().hex
        self.lane, self.bound = RLock(), Event()
        self.records, self.samples, self.disposals, self.workers = [], [], [], []
        self.job, self.child, self.session = Job.new(), None, None
        self.started = self.finished = False
        self.captured = self.abort_requested = False
        self.error = self.job_state = None
        self.adapter = load_adapter()
        self.recorder = Recorder(self.adapter, config['executable'], 'http://127.0.0.1:' + str(unused_port()), folder)
        self.recorder.__enter__()
        self.open_recorded = self.adapter.http.urlopen
        self.original_transport = self.adapter._transport.OwnedHttpTransport

        def open_response(request, **kwargs):
            """Opt in only this case's exact chat request to resumable streaming."""
            if (case == 'stream-delete' and getattr(request, 'full_url', '') ==
                    self.recorder.base_url + '/v1/chat/completions'):
                request.add_header('X-Conversation-Id', self.conv_id)
            return self.open_recorded(request, **kwargs)

        self.adapter.http.urlopen = open_response
        probe = self
        original_transport = self.original_transport

        class CaseTransport(original_transport):
            """Add only case-specific request/header/wait observation over recorded production IO."""

            def open(self, request, **kwargs):
                """Opt in this private DELETE case, or observe the pre-header raw wait."""
                is_chat = request.full_url.endswith('/v1/chat/completions')
                if is_chat and case == 'stream-delete':
                    request.add_header('X-Conversation-Id', probe.conv_id)
                if is_chat and case == 'pre-header-cancel':
                    actual = self.transport
                    original_wait = actual.waitSocket

                    def wait(channel, deadline, *, writing=False):
                        """Signal actual header receive wait without changing socket bytes or IO."""
                        if not writing and not probe.bound.is_set():
                            probe.bound.set()
                            probe.record('pre-header-read-wait-entered')
                        return original_wait(channel, deadline, writing=writing)

                    actual.waitSocket = wait
                    probe.launch_worker(probe.pre_header_cancel)
                return super().open(request, **kwargs)

        self.adapter._transport.OwnedHttpTransport = CaseTransport
        self.validation = Validation(config, folder, self.recorder, self.adapter)
        # Keep exactly one ApplicationRun for these cases, without provider IO.
        self.validation.host.closeApplicationRun(self.validation.runtimes[1].applicationRun.applicationRunId)
        self.original_stream = self.adapter._Session.stream

        def stream(session, request):
            """Intercept the first yielded event, preserving the issued admission/session."""
            iterator = self.original_stream(session, request)
            if self.case == 'pre-header-cancel':
                self.session = session
            try:
                for event in iterator:
                    if self.session is None and event.eventType != 'completed':
                        self.session = session
                        self.at_boundary(session)
                        if self.case == 'early-close' and self.captured:
                            iterator.close()  # Reader thread owns the suspended generator.
                            return
                    yield event
            except BaseException as error:
                self.record('provider-stream-exit', exceptions=exception_facts(error))
                (self.folder / 'stream-exit.private.txt').write_text(
                    ''.join(traceback.format_exception(error)), encoding='utf-8')
                raise
            finally:
                iterator.close()

        self.adapter._Session.stream = stream
        self.save()

    def record(self, kind, **values):
        """Persist bounded private event facts without holding a lane across provider IO."""
        with self.lane:
            if len(self.records) < 256:
                self.records.append({'kind': kind, 'at': time.monotonic(), **values})
            self.save()

    def save(self):
        """Retain evidence under the ignored private case folder, including tombstones."""
        with self.lane:
            write_json(self.folder / 'probe.private.json', {
                'case': self.case, 'token': self.token, 'conversationId': self.conv_id,
                'events': self.records, 'samples': self.samples, 'disposals': self.disposals,
                'jobState': self.job_state, 'error': self.error,
            })

    def launch_worker(self, target):
        """Start a rooted daemon so stuck IO never monopolizes the operator command loop."""
        worker = Thread(target=target, daemon=True)
        self.workers.append(worker)
        worker.start()

    def run(self):
        """Accept one explicit operator run command, with no retries or later-domain reuse."""
        if self.started:
            raise RuntimeError('A probe case can be run only once.')
        self.started = True
        self.launch_worker(self.infer)

    def infer(self):
        """Launch one explicit profile and one admitted Job; never auto-dispose its child."""
        try:
            self.validation.call(0, 'transition', 'A')
            if len(self.recorder.processes) != 1:
                raise RuntimeError('Expected exactly one owned launch.')
            self.child = OwnedChild.capture(self.recorder.processes[0])
            self.generation = self.validation.generation()
            self.baseline = self.slots()
            if self.baseline.get('is_processing') is not False:
                raise RuntimeError('The sole owned slot was not idle before the request.')
            self.record('owned-start', pid=self.child.pid, handle=self.child.handle,
                        generation=self.generation, baseline=self.baseline)
            payload = {'model': 'A', 'prompt': self.config['prompt'], 'options': {
                'temperature': 0.0, 'maxTokens': self.config['maxTokens'],
                'timeoutSeconds': self.config['timeoutSeconds'], 'reasoningFormat': 'deepseek'}}
            result = self.validation.runtimes[0].runJob('er04.run@1', payload, job=self.job)
            self.job_state = result.state.value
            self.record('job-ended', job=plain(result))
        except Exception as error:
            self.error = type(error).__name__
            (self.folder / 'failure.private.txt').write_text(traceback.format_exc(), encoding='utf-8')
            self.record('worker-error', type=self.error)
        finally:
            if self.session is not None:
                self.record('adapter-cleanup', nativeTask=self.session.nativeTask,
                            effect=plain(self.session.cleanupEffect), readerDone=self.session.readerDone.is_set())
            self.finished = True
            self.record('inference-worker-exited')

    def slots(self):
        """Observe only the exact retained live process/generation through its own endpoint."""
        self.child.require(self.recorder.processes)
        if self.child.process.poll() is not None or self.validation.generation() != self.generation:
            raise RuntimeError('Owned process/generation changed.')
        request = self.adapter.http.Request(self.recorder.base_url + '/slots')
        with self.adapter.http.urlopen(request, timeout=1) as response:
            value = json.loads(response.read())
        self.child.require(self.recorder.processes)
        if self.child.process.poll() is not None or self.validation.generation() != self.generation:
            raise RuntimeError('Owned process/generation changed during observation.')
        if type(value) is not list or len(value) != 1 or value[0].get('id') != 0:
            raise RuntimeError('Unexpected slot ownership shape.')
        return value[0]

    def at_boundary(self, session):
        """Capture native active-task binding, then perform only the operator-selected abort."""
        active = self.slots()
        if active.get('is_processing') is not True or type(active.get('id_task')) is not int or session.nativeDone:
            self.record('inconclusive-before-active-capture')
            return
        self.native_task = active['id_task']
        self.captured = True
        self.record('active-bound', nativeTask=self.native_task, completionId=session.completionId, slot=active)
        self.abort_requested = True
        self.record('abort-requested', case=self.case)
        self.launch_worker(self.monitor)
        if self.case in ('job-cancel', 'blocked-job-cancel'):
            if self.case == 'blocked-job-cancel':
                actual = session.transport.transport
                original_wait = actual.waitSocket

                def wait(channel, deadline, *, writing=False):
                    """Expose the actual buffered raw read wait after the active task was captured."""
                    if not writing and not self.bound.is_set():
                        self.record('blocked-body-read-wait-entered')
                        self.bound.set()
                    return original_wait(channel, deadline, writing=writing)

                actual.waitSocket = wait
            self.launch_worker(self.cancel_job)
            # Wait for signal visibility, not callback completion/transport drain.
            if self.case == 'job-cancel' and not self.bound.wait(2):
                raise RuntimeError('Cancellation control worker did not enter.')
        elif self.case == 'disconnect':
            session.response.close()
            self.record('local-http-close-returned')
        elif self.case == 'read-timeout':
            # Test-only socket control on the actual retained response. No fake
            # TimeoutError, proxy, foreign socket, new connection or extra request.
            channel = session.transport.transport._socket
            if channel.getpeername() != ('127.0.0.1', int(self.recorder.base_url.rsplit(':', 1)[1])):
                raise RuntimeError('The captured response socket peer is not the owned endpoint.')
            session.transport.setReadTimeout(0.01)
            self.record('owned-socket-read-timeout-set', seconds=0.01)
        elif self.case == 'stream-delete':
            self.launch_worker(self.delete_stream)

    def cancel_job(self):
        """Issue the actual Job cancellation from a separate supervised control thread."""
        if self.case == 'blocked-job-cancel':
            if not self.bound.wait(2):
                self.record('inconclusive-no-blocked-body-read')
                return
        else:
            self.bound.set()
        try:
            self.job.requestCancellation()
            self.record('job-cancellation-returned')
        except Exception as error:
            self.record('job-cancellation-error', type=type(error).__name__)

    def pre_header_cancel(self):
        """Capture a real active task during header wait; missing production binding stays unknown."""
        if not self.bound.wait(2):
            self.record('inconclusive-no-pre-header-wait')
            return
        try:
            active = self.slots()
            if (self.session.response is not None or active.get('is_processing') is not True or
                    type(active.get('id_task')) is not int):
                self.record('inconclusive-pre-header-boundary-lost')
                return
            self.native_task, self.captured = active['id_task'], True
            self.record('active-bound', nativeTask=self.native_task, slot=active, completionId=None)
            self.abort_requested = True
            self.record('abort-requested', case=self.case, headersReturned=self.session.response is not None)
            self.launch_worker(self.monitor)
            self.job.requestCancellation()
            self.record('job-cancellation-returned')
        except Exception as error:
            self.record('pre-header-control-error', type=type(error).__name__)

    def delete_stream(self):
        """Request deletion of only this operation's exact private resumable-session key."""
        try:
            lookup = self.adapter.http.Request(self.recorder.base_url + '/v1/streams/lookup',
                data=json.dumps({'conversation_ids': [self.conv_id]}).encode(),
                headers={'Content-Type': 'application/json'}, method='POST')
            with self.adapter.http.urlopen(lookup, timeout=1) as response:
                sessions = json.loads(response.read())
            if len(sessions) != 1 or sessions[0].get('conversation_id') != self.conv_id:
                raise RuntimeError('Exact private resumable session was not found.')
            self.record('exact-resumable-session-found', session=sessions[0])
            request = self.adapter.http.Request(self.recorder.base_url + '/v1/stream?' +
                urlencode({'conv_id': self.conv_id}), method='DELETE')
            with self.adapter.http.urlopen(request, timeout=1) as response:
                self.record('stream-delete-returned', status=response.status)
        except Exception as error:
            self.record('stream-delete-error', type=type(error).__name__)

    def monitor(self):
        """Bound read-only task observations independently of network/stop workers."""
        deadline = time.monotonic() + 10
        consecutive_idle = 0
        while time.monotonic() < deadline:
            try:
                slot = self.slots()
                sample = {'at': time.monotonic(), 'slot': slot}
                with self.lane:
                    self.samples.append(sample)
                same_task = slot.get('id_task') == self.native_task
                if not same_task:
                    self.record('task-correlation-lost')
                    return
                consecutive_idle = consecutive_idle + 1 if slot.get('is_processing') is False else 0
                self.save()
                if consecutive_idle >= 2:
                    self.record('same-task-idle-twice')
                    return
            except Exception as error:
                self.record('monitor-error', type=type(error).__name__)
                return
            Event().wait(0.2)
        self.record('monitor-deadline')

    def status(self):
        """Project structural observations only; never export raw model output or task IDs."""
        snapshots = plain(self.validation.host.executionResources.snapshots)
        self.record('claim-snapshots', snapshots=snapshots)
        facts = transport_facts(self.recorder.requests)
        text = (self.folder / 'server.log').read_text(encoding='utf-8', errors='replace')[-131072:]
        task = getattr(self, 'native_task', None)
        import re
        released = task is not None and any(int(value) == task for value in
            re.findall(r'slot\s+release:\s+id\s+0\s*\|\s*task\s+(\d+)\s*\|\s*stop processing', text))
        cancelled = task is not None and f'cancel task, id_task = {task}\n' in text.replace('\r\n', '\n')
        result = {'case': self.case, 'started': self.started, 'workerFinished': self.finished,
            'activeTaskCaptured': self.captured, 'abortRequested': self.abort_requested,
            'sameTaskIdleTwice': any(x['kind'] == 'same-task-idle-twice' for x in self.records),
            'exactTaskReleaseLogged': released, 'exactTaskCancelLogged': cancelled,
            'jobState': self.job_state, 'workerError': self.error,
            'resourceStates': [x['state'] for x in snapshots],
            'liveWorkers': sum(worker.is_alive() for worker in self.workers),
            'childLive': self.child is not None and self.child.process.poll() is None,
            'disposals': self.disposals, 'wire': facts,
            'controlEvents': [x['kind'] for x in self.records if x['kind'] != 'claim-snapshots']}
        write_json(self.folder / 'summary.json', result)
        return result

    def emergency(self, action):
        """Dispose only after an explicit token-bound operator command; no automatic escalation."""
        if self.child is None or not self.started:
            raise RuntimeError('No captured active-abort child is eligible for emergency disposal.')
        states = [item.state.value for item in self.validation.host.executionResources.snapshots]
        observation_finished = any(x['kind'] in ('same-task-idle-twice', 'monitor-deadline',
            'monitor-error', 'task-correlation-lost') for x in self.records)
        observation_finished = observation_finished or (self.finished and not self.captured)
        if not observation_finished:
            raise RuntimeError('Read-only graceful observation is still in progress.')
        if not self.finished and not any(x['kind'] == 'monitor-deadline' for x in self.records):
            raise RuntimeError('Wait for the graceful observation to finish before disposal.')
        if 'unknown' not in states and not any(x['kind'] == 'monitor-deadline' for x in self.records):
            raise RuntimeError('Emergency disposal requires unknown claim or failed graceful observation.')
        self.record('operator-disposal-command', action=action)
        self.disposals.append(dispose(self.child, self.recorder.processes, action, self.disposals))
        self.save()

    def finish(self):
        """Exit only after actual child exit and all worker returns; preserve unknown tombstones."""
        if any(worker.is_alive() for worker in self.workers):
            raise RuntimeError('Live probe workers still require supervision.')
        if self.child is not None and self.child.process.poll() is None:
            states = [item.state.value for item in self.validation.host.executionResources.snapshots]
            if 'unknown' in states:
                raise RuntimeError('Unknown child requires explicit operator disposal before exit.')
            if not self.validation.close():
                raise RuntimeError('Ordinary completed-case cleanup did not confirm exit.')
        self.adapter._Session.stream = self.original_stream
        self.adapter.http.urlopen = self.open_recorded
        self.adapter._transport.OwnedHttpTransport = self.original_transport
        self.recorder.__exit__(None, None, None)


def main():
    """Hold a private one-case process until explicit run/status/disposal/finish commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--case', required=True, choices=CASES)
    args = parser.parse_args()
    config = load_config(args.config)
    if config['contextTokens'] > 1024 or config['maxTokens'] > 24 or config['threads'] > 4:
        parser.error('Approved probe ceilings are context 1024 / tokens 24 / threads 4.')
    folder = ROOT / 'python-embedded/er04-local' / ('abort-' + args.case + '-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    write_json(folder / 'config.private.json', config)
    executable = Path(config['executable'])
    version = subprocess.run([str(executable), '--version'], capture_output=True, timeout=20,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    identity = {'sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                'version': (version.stdout + version.stderr).decode(errors='replace')}
    write_json(folder / 'binary.private.json', identity)
    if version.returncode or identity['sha256'] != 'f727ec0b2b18095982433b86768ce49470e79008170936d5656143b870109493':
        parser.error('The supplied executable differs from the approved actual binary.')
    probe = Probe(config, folder, args.case)
    print(json.dumps({'ready': True, 'case': args.case, 'operatorToken': probe.token,
                      'commands': ['run TOKEN', 'status', 'terminate TOKEN', 'kill TOKEN', 'finish TOKEN']}), flush=True)
    while True:
        line = sys.stdin.readline()
        if not line:
            # EOF cannot become an implicit disposal/parent-exit workaround.
            print(json.dumps({'blocked': 'operator-input-closed-ownership-retained'}), flush=True)
            while True:
                Event().wait(1)
        command = line.strip().split()
        try:
            if command == ['status']:
                print(json.dumps(probe.status()), flush=True)
                continue
            if len(command) != 2 or command[1] != probe.token:
                raise ValueError('An exact operator token is required.')
            if command[0] == 'run':
                probe.run()
            elif command[0] in ('terminate', 'kill'):
                probe.emergency(command[0])
            elif command[0] == 'finish':
                probe.finish()
                summary = probe.status()
                print(json.dumps({'finished': True, 'summary': summary}), flush=True)
                return 0
            else:
                raise ValueError('Unknown operator command.')
            print(json.dumps({'ack': command[0]}), flush=True)
        except Exception as error:
            print(json.dumps({'commandRejected': type(error).__name__, 'reason': str(error)}), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
