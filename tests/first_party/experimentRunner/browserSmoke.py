# file: tests/first_party/experimentRunner/browserSmoke.py ; version: 1
"""Explicit actual-browser App PM/G harness; synthetic held IO through managed llama.cpp."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

from pytest import MonkeyPatch

from tests.first_party.experimentRunner.test_archive import REAL_POPEN, readArchive
from tests.first_party.experimentRunner.test_experimentRunner import rig, execute
from tests.first_party.experimentRunner.test_observations import host4, observations, snapshots
from tools.observationViewer.server import ViewerServer


class HeldStream:
    """Deliver genuine provider SSE then hold delivery without replacing runtime lifecycle."""

    def __init__(self, lines, released):
        """Retain finite synthetic SSE bytes and one test-only delivery latch."""
        self.lines, self.released = lines, released

    def __iter__(self):
        """Expose the first real delta then wait for explicit browser fixture control."""
        yield self.lines[0]
        if not self.released.wait(25):
            raise RuntimeError('Browser fixture delivery timed out.')
        yield from self.lines[1:]


def main(argv=None):
    """Launch only explicitly supplied browser/tool paths; private notice goes over child stdin."""
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('node', 'playwright', 'browser', 'screenshots'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args(argv)
    output = Path(args.screenshots)
    output.mkdir(parents=True, exist_ok=False)
    driver = Path(__file__).with_name('browser-smoke.cjs')
    child = REAL_POPEN([args.node, str(driver), args.playwright, args.browser, str(output)],
                       stdin=-1, stdout=-1, stderr=-1, text=True)
    firstReleased, secondReleased, secondReached, finished = Event(), Event(), Event(), Event()
    result, calls = {}, []
    server = ViewerServer(0)
    serverThread = Thread(target=server.serve_forever, daemon=True)
    serverThread.start()
    try:
        with TemporaryDirectory(prefix='experiment-browser-') as directory, MonkeyPatch.context() as patch:
            environment = rig.__wrapped__(Path(directory), patch)
            host = host4(environment, live=True)
            host['observation']['live'].update(allowedOrigin=server.origin, maxLifetimeSeconds=30)
            environment[0]['streams'][0] = HeldStream(environment[0]['streams'][0], firstReleased)
            def onCall(index):
                """Hold the independent second call while the first source has post-call lifetime."""
                if index == 1:
                    secondReached.set()
                    if not secondReleased.wait(25):
                        raise RuntimeError('Browser fixture suite timed out.')
            environment[0]['onCall'] = onCall
            original = observations.Controller.begin
            def begin(controller, ctx, port, turnId):
                """Retain only observation-only state for explicit test revocation."""
                call = original(controller, ctx, port, turnId)
                if call is not None:
                    calls.append(call)
                return call
            patch.setattr(observations.Controller, 'begin', begin)
            def send(value):
                """Send framed private test input without logging or persisting the descriptor."""
                child.stdin.write(json.dumps(value) + '\n')
                child.stdin.flush()
            def handoff(notice):
                """Provide the real one-use transient host callback directly to the browser driver."""
                send(dict(origin=server.origin, **notice.value()))
            def runSuite():
                """Exercise actual runtime/provider/PM/G composition and own all its teardown."""
                try:
                    result['value'], _ = execute(environment, observationHandoff=handoff)
                except BaseException:
                    result['failed'] = True
                finally:
                    finished.set()
            worker = Thread(target=runSuite, name='experiment-browser-suite', daemon=True)
            worker.start()
            summary = None
            while True:
                line = child.stdout.readline()
                if not line:
                    break
                message = json.loads(line)
                action = message.get('action')
                if action == 'verify-active':
                    assert environment[0]['calls'] == 1 and calls[0].active
                    assert calls[0].capture.snapshot().sessionId and not calls[0].viewer.revoked
                    send(dict(ok=True))
                elif action == 'release-call':
                    firstReleased.set()
                    assert secondReached.wait(5)
                    assert not calls[0].active and calls[0].sessionRelease == 'pending'
                    send(dict(ok=True))
                elif action == 'revoke':
                    calls[0].expire()
                    assert calls[0].capture.revoked and calls[0].sessionRelease == 'completed'
                    assert environment[0]['calls'] == 2
                    send(dict(ok=True))
                elif action == 'finish':
                    secondReleased.set()
                    assert finished.wait(8) and not result.get('failed')
                    value = result['value']
                    assert value['suite']['status'] == 'succeeded' and readArchive(value)['status'] == 'sealed'
                    assert snapshots(value)[0]['availability'] == 'available'
                    send(dict(ok=True))
                elif message.get('result') == 'passed':
                    summary = message
                else:
                    raise AssertionError('Unexpected browser control.')
            child.wait(timeout=10)
            assert child.returncode == 0 and summary is not None, 'Browser driver failed; private diagnostics withheld.'
            worker.join(5)
            assert not worker.is_alive()
            summary.update(managedInferenceCalls=environment[0]['calls'], realModel=False,
                           archiveStatus='sealed', viewerSourceUnchanged=True,
                           callFinalSnapshotAvailable=True, allSyntheticChildrenExited=all(p.returncode == 0 for p in environment[0]['processes']))
            (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
            print(json.dumps(summary))
    finally:
        firstReleased.set()
        secondReleased.set()
        server.shutdown()
        server.server_close()
        serverThread.join(3)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


if __name__ == '__main__':
    main()
