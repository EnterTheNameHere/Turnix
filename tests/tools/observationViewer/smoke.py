# file: tests/tools/observationViewer/smoke.py ; version: 1
"""Explicit synthetic verification harness: real G-01 activation, CORS and local browser polling."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from threading import Thread

from backend.context import CodeEntryIdentity
from backend.llm.streamObservation import StreamOutcome
from backend.observation import ObservationLifetime, ObservationRequest
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.registration import RegistrationScope
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore
from backend.telemetry import CpuStaticObservation, CpuUtilizationObservation, GpuObservation, SignalUnavailable, SystemMemoryObservation, TelemetryService
from tools.observationViewer.server import ViewerServer

ROOT = Path(__file__).resolve().parents[3]
SECOND = 1_000_000_000


class Clock:
    """Deterministic shared observation domain; display wall time can jump independently."""

    def __init__(self):
        """Uses nanoseconds beyond JavaScript Number precision."""
        self.now = 2**60 + 123
        self.wall = 1_793_534_400_000_000_000

    def read(self):
        """Returns synthetic monotonic time without sampling hardware."""
        return self.now

    def display(self):
        """Returns display-only synthetic Unix wall time."""
        return self.wall


class Hardware:
    """Synthetic whole-host metrics with missing sensors and nonzero collection duration."""

    def __init__(self, clock):
        """Initializes finite deterministic signal sequences and read counts."""
        self.clock, self.index, self.reads = clock, 0, 0

    def primeCpu(self):
        """Provides deterministic CPU preparation without native access."""

    def cpuStatic(self):
        """Returns synthetic topology."""
        return CpuStaticObservation(8, 16)

    def cpuUtilization(self):
        """Models changing utilization while retaining percent units."""
        value = [18, 25, 44, 71, 85, 63, 49, 30][self.index % 8]
        return CpuUtilizationObservation(value, (value,) * 16)

    def systemMemory(self):
        """Models host RAM bytes rather than process-attributed usage."""
        used = (10 + self.index % 7) * 2**30
        return SystemMemoryObservation(32 * 2**30, 32 * 2**30 - used, used, 2 * 2**30, used / (32 * 2**30) * 100)

    def swap(self):
        """Reports explicit unavailable swap evidence."""
        return SignalUnavailable("synthetic unavailable")

    def gpu(self):
        """Models delayed sequential reads and deliberately unavailable GPU intervals."""
        self.reads += 1
        self.clock.now += SECOND // 5
        if self.index % 12 in (7, 8):
            return SignalUnavailable("synthetic missing device")
        percent = [22, 48, 72, 89, 77, 50, 35, 18][self.index % 8]
        used = (5 + self.index % 3) * 2**30
        absent = SignalUnavailable("synthetic missing sensor")
        return GpuObservation(0, "not published", 16 * 2**30, used, 16 * 2**30 - used, percent, 30,
                              absent, absent, absent, absent, absent, absent, absent)

    def close(self):
        """Has no real hardware resource to release."""


class ManualTelemetry(TelemetryService):
    """Test-only deterministic advancement of the actual accepted collector/subscription code."""

    def _ensureWorker(self):
        """Leaves physical collection to explicit synthetic test steps, not a background clock."""


def run(args) -> dict:
    """Runs the actual Pack/browser smoke using a private stdin/stdout test-control protocol."""
    with tempfile.TemporaryDirectory(prefix="actant-g02-") as folder, ViewerServer(0) as viewer:
        viewerWorker = Thread(target=viewer.serve_forever, kwargs={"poll_interval": 0.05})
        viewerWorker.start()
        app = Path(folder) / "app"
        app.mkdir()
        (app / "manifest.json").write_text(json.dumps({"packId": "test.viewer", "kind": "appPack", "version": "1.0.0", "codeEntries": []}), encoding="utf-8")
        clock = Clock()
        hardware = Hardware(clock)
        telemetry = ManualTelemetry(machineProvider=hardware, gpuProvider=hardware, monotonicNs=clock.read, wallTimeNs=clock.display)
        host = RuntimeHost(applicationStore=ApplicationStore(Path(folder) / "saves"),
                           packResolver=PackResolver(roots=(app, ROOT / "first-party" / "observationBridge")), telemetryService=telemetry)
        host._observations._clock = clock.read
        host.start()
        process = None
        handler = originalValidate = None
        try:
            runtime = host.createApplication(appPackId="test.viewer", plan=ManualActivationPlan(packIds=("test.viewer", "observation.bridge")),
                                             config={"observationBridge": {"allowedOrigins": [viewer.origin]}})
            identity = CodeEntryIdentity(runtime.applicationRun.application.applicationId, runtime.applicationRun.applicationRunId,
                                         "test.viewer", "1.0.0", "test", "viewer-entry", "source", "python-source@1", "synthetic")
            ctx = runtime.createContext(identity=identity, packRoot=app, registrationScope=RegistrationScope())
            source = ctx.observations.request(ObservationRequest(historySamples=32, historyWindows=24), lifetime=ObservationLifetime.APPLICATION)
            claim = source._stream.begin()
            origin = clock.now
            for index in range(40):
                hardware.index = index
                clock.now = origin + index * SECOND + SECOND // 10
                count = [120, 240, 510, 680, 400, 160, 0, 0, 220, 550][index % 10]
                if count:
                    claim.recordText("x" * count)
                clock.wall = 1_793_534_400_000_000_000 + index * SECOND - (3600 * SECOND if index >= 25 else 0)
                telemetry.sample()
            clock.now = origin + 40 * SECOND - 1
            claim.finish(StreamOutcome.COMPLETED)
            grant = ctx.observations.export(source)
            publication = ctx.capabilities.call("observation.bridge.publish@1", {"grant": grant})
            connection = ctx.capabilities.call("observation.bridge.connection@1")
            ctx.invalidate()
            initialReads = hardware.reads
            bridge = next(pack.entries[0].state for pack in runtime.packLoader._loadedPacks if pack.pack.packId == "observation.bridge")
            handler = bridge._server.RequestHandlerClass
            originalValidate = handler._validate
            observedHeaders = []
            def validate(request, **kwargs):
                """Records only nonsecret CORS/header names to diagnose real-browser compatibility."""
                observedHeaders.append({"method": request.command, "origin": request.headers.get("Origin"),
                                        "requestedHeaders": request.headers.get("Access-Control-Request-Headers")})
                return originalValidate(request, **kwargs)
            handler._validate = validate
            evidence = args.output.resolve()
            evidence.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen([args.node, str(Path(__file__).with_name("browser-smoke.cjs")), str(args.playwright.resolve()),
                                        str(args.browser.resolve()), str(evidence)], cwd=ROOT, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            process.stdin.write(json.dumps({"origin": viewer.origin, "connection": connection, "publication": publication}) + "\n")
            process.stdin.flush()
            result = None
            for line in process.stdout:
                message = json.loads(line)
                if message.get("action") == "verify-claims":
                    assert len(telemetry._subscriptions) == 1 and hardware.reads == initialReads
                elif message.get("action") == "revoke":
                    grant.revoke()
                elif message.get("action") == "withdraw":
                    runtime.invokeCapability("observation.bridge.withdraw@1", {"publicationId": publication["publicationId"]})
                elif message.get("action") == "close-bridge":
                    runtime.packLoader.close()
                elif message.get("result") == "passed":
                    result = message
                    continue
                else:
                    raise RuntimeError("Unknown browser test message.")
                process.stdin.write('{"ok":true}\n')
                process.stdin.flush()
            process.wait(timeout=15)
            if process.returncode != 0 or result is None:
                raise RuntimeError("Browser smoke failed; " + process.stderr.read() + json.dumps(observedHeaders[-5:]))
            assert hardware.reads == initialReads and len(telemetry._subscriptions) == 1
            result["syntheticHardwareReads"] = initialReads
            result["sourceClaimsAfterReaders"] = 1
            (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result
        finally:
            if handler is not None and originalValidate is not None:
                handler._validate = originalValidate
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            host.stop()
            telemetry.stop()
            viewer.shutdown()
            viewerWorker.join(5)
            assert not viewerWorker.is_alive()


def main() -> None:
    """Requires explicit local test runtime/browser paths and an evidence output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--playwright", type=Path, required=True)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
