# file: tests/first_party/observationBridge/test_bridge.py ; version: 1
"""G-01 architectural tests using actual Pack activation and ephemeral authenticated HTTP."""
from __future__ import annotations

import gc
from http.client import HTTPResponse
import importlib
import json
import socket
import shutil
import sys
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.client import HTTPMessage
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from backend.context import CodeEntryContext, CodeEntryIdentity
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.observation import ObservationGrantRevoked, ObservationLifetime, ObservationRequest, ObservationService
from backend.orchestration import JobState
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.registration import RegistrationScope
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore
from backend.telemetry import (
    CpuStaticObservation, CpuUtilizationObservation, GpuObservation, SignalUnavailable,
    SwapObservation, SystemMemoryObservation, TelemetryService,
)

PACK_ROOT = Path(__file__).resolve().parents[3] / "first-party" / "observationBridge"
_entrySpec = importlib.util.spec_from_file_location("observationBridgeTestEntry", PACK_ROOT / "codeEntry.py")
_entry = importlib.util.module_from_spec(_entrySpec)
_entrySpec.loader.exec_module(_entry)
bridgeModule = _entry._implementation
schema = sys.modules[bridgeModule.__package__ + ".schema"]
SECOND = 1_000_000_000
SECRET = "DO_NOT_PUBLISH_prompt_response_provider_diagnostic"
ORIGIN = "http://127.0.0.1:7777"


class Clock:
    """Large exact monotonic epoch and independently jumping display clock."""

    def __init__(self):
        """Starts beyond JavaScript Number precision to expose lossy JSON representations."""
        self.now = 2**60 + 123
        self.wall = 2**61 + 456

    def read(self):
        """Returns the host observation domain without implicit advancement."""
        return self.now

    def display(self):
        """Returns wall evidence independently of monotonic elapsed time."""
        return self.wall


class Hardware:
    """Deterministic graph measurements; provider text contains a non-public sentinel."""

    def __init__(self, clock):
        """Tracks actual collection visits and resource cleanup without native providers."""
        self.clock = clock
        self.reads = 0
        self.closes = 0

    def primeCpu(self):
        """Provides an inactive fake CPU preparation seam."""

    def cpuStatic(self):
        """Returns deterministic topology without hardware access."""
        return CpuStaticObservation(8, 16)

    def cpuUtilization(self):
        """Provides finite percent values with an intentionally oversized per-core vector."""
        return CpuUtilizationObservation(12.5, (25.0,) * 300)

    def systemMemory(self):
        """Supplies byte amounts too large for exact JavaScript Number representation."""
        return SystemMemoryObservation(2**56 + 1, 2**55 + 3, 2**54 + 5, 2**53 + 7, 25.0)

    def swap(self):
        """Preserves zero as an available measurement distinct from a missing signal."""
        return SwapObservation(100, 0, 100, 0.0)

    def gpu(self):
        """Models sequential collection duration and private provider label/diagnostic fields."""
        self.reads += 1
        self.clock.now += 17
        missing = SignalUnavailable(SECRET)
        return GpuObservation(0, SECRET, 2**55 + 9, 1024, 2048, 50.0, 20.0,
                              missing, 10.0, 20.0, 900, 800, SECRET, missing)

    def close(self):
        """Records cleanup to prove grants/readers do not close borrowed providers."""
        self.closes += 1


class Provider:
    """Normal LLM provider with secret content and native metadata outside observation."""

    def __init__(self, clock, telemetry):
        """Schedules real generic delivery observations with a delayed hardware sample."""
        self.clock, self.telemetry = clock, telemetry

    def getExecutionProfile(self, **_kwargs):
        """Provides the ordinary execution profile without engine instrumentation."""
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request):
        """Delivers real text, a silence gap, then native metadata that must never be serialized."""
        self.clock.now += SECOND // 4
        yield LlmStreamEvent(eventType="delta", text=SECRET)
        self.clock.now += 3 * SECOND
        self.clock.wall -= 10 * SECOND
        self.telemetry.sample()
        yield LlmStreamEvent(eventType="delta", text="é🙂")
        yield LlmStreamEvent(eventType="completed", metadata={"private": SECRET, "usage": {"completion_tokens": 7}})


@pytest.fixture
def environment(tmp_path, monkeypatch):
    """Builds a real RuntimeHost; deterministic manual polling avoids time-based test races."""
    def noWorker(_service):
        """Lets tests control physical collection while retaining real subscription logic."""
    monkeypatch.setattr(TelemetryService, "_ensureWorker", noWorker)
    app = tmp_path / "app"
    app.mkdir()
    (app / "manifest.json").write_text(json.dumps({"packId": "test.app", "kind": "appPack", "version": "1.0.0", "codeEntries": []}), encoding="utf-8")
    clock = Clock()
    hardware = Hardware(clock)
    telemetry = TelemetryService(machineProvider=hardware, gpuProvider=hardware, monotonicNs=clock.read, wallTimeNs=clock.display)
    host = RuntimeHost(applicationStore=ApplicationStore(tmp_path / "saves"), packResolver=PackResolver(roots=(app, PACK_ROOT)), telemetryService=telemetry)
    host._observations._clock = clock.read
    host.start()
    yield host, clock, hardware, telemetry
    host.stop()
    telemetry.stop()


def application(environment, *, bridge=True, config=None):
    """Activates the actual optional modPack only when explicitly selected."""
    host, _clock, _hardware, _telemetry = environment
    return host.createApplication(appPackId="test.app", plan=ManualActivationPlan(packIds=("test.app", "observation.bridge") if bridge else ("test.app",)),
                                  config={"observationBridge": {"allowedOrigins": [ORIGIN]}} if config is None else config)


def context(runtime):
    """Creates a real fresh application invocation with registered test CodeEntry identity."""
    identity = CodeEntryIdentity(runtime.applicationRun.application.applicationId, runtime.applicationRun.applicationRunId,
                                 "test.app", "1.0.0", "test", "test-entry", "source", "python-source@1", "implementation")
    if "test-entry" not in runtime._codeEntries:
        runtime.registerCodeEntry(identity, Path.cwd())
    return runtime.createContext(identity=identity, packRoot=Path.cwd(), registrationScope=RegistrationScope())


def state(runtime):
    """Inspects only test-owned Pack state to verify actual endpoint teardown."""
    return next(pack.entries[0].state for pack in runtime.packLoader._loadedPacks if pack.pack.packId == "observation.bridge")


def publish(runtime, *, request=None, lifetime=ObservationLifetime.APPLICATION):
    """Exports and publishes through actual fresh-context capability mediation."""
    ctx = context(runtime)
    session = ctx.observations.request(ObservationRequest(historySamples=2, historyWindows=2) if request is None else request, lifetime=lifetime)
    grant = ctx.observations.export(session)
    descriptor = ctx.capabilities.call("observation.bridge.publish@1", {"grant": grant})
    connection = ctx.capabilities.call("observation.bridge.connection@1")
    return ctx, session, grant, descriptor, connection


def http(connection, publication, *, credential=True, origin=None, host=None, path=None, method="GET", extra=()):
    """Uses a genuine HTTP client with exact optional malformed-header controls."""
    endpoint = urlsplit(connection["endpoint"])
    target = path if path is not None else "/publications/" + publication["publicationId"]
    lines = [f"{method} {target} HTTP/1.1", "Host: " + (endpoint.netloc if host is None else host)]
    if credential is not False:
        lines.append("Authorization: Bearer " + (connection["readCredential"] if credential is True else credential))
    if origin is not None:
        lines.append("Origin: " + origin)
    lines.extend(key + ": " + value for key, value in extra)
    lines.append("Connection: close")
    with socket.create_connection((endpoint.hostname, endpoint.port), timeout=3) as client:
        client.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin1"))
        response = HTTPResponse(client)
        response.begin()
        status, headers, body = response.status, dict(response.getheaders()), response.read()
        response.close()
    return status, headers, body


def test_explicit_pack_activation_observes_nothing_and_shuts_down(environment):
    """Activation owns an endpoint but allocates no session, collector or automatic inference observer."""
    runtime = application(environment, bridge=False)
    with pytest.raises(LookupError):
        runtime.invokeCapability("observation.bridge.connection@1")
    runtime.packLoader.activatePack(runtime.packLoader._resolver.requireSingle("observation.bridge"))
    bridge = state(runtime)
    connection = runtime.invokeCapability("observation.bridge.connection@1")
    assert urlsplit(connection["endpoint"]).hostname == "127.0.0.1"
    assert not environment[3]._subscriptions and environment[2].reads == 0
    assert not runtime._observationOwner._sessions
    worker = bridge._thread
    runtime.packLoader.close()
    assert not worker.is_alive() and bridge._closed and not bridge._publications
    with pytest.raises(LookupError):
        runtime.invokeCapability("observation.bridge.connection@1")


def test_grants_are_read_only_context_free_and_source_revocable(environment):
    """APPLICATION export outlives its invocation without extending source or execution authority."""
    runtime = application(environment)
    ctx, session, grant, publication, connection = publish(runtime)
    reference = weakref.ref(ctx)
    assert not any(hasattr(grant, name) for name in ("release", "forExecution", "subscribe", "owner", "session", "__dict__"))
    saved = grant.snapshot()
    ctx.invalidate()
    with pytest.raises(RuntimeError, match="no longer valid"):
        ctx.observations.export(session)
    with pytest.raises(RuntimeError, match="no longer valid"):
        ctx.observations.bindReadGrant(grant)
    del ctx
    gc.collect()
    assert reference() is None
    assert http(connection, publication)[0] == 200
    session.release()
    assert grant.revoked
    with pytest.raises(ObservationGrantRevoked):
        grant.snapshot()
    assert http(connection, publication)[0] == 410
    assert saved.observation.hardware
    assert next(iter(state(runtime)._publications.values())).response is None


@pytest.mark.parametrize("lifetime", [ObservationLifetime.RUN, ObservationLifetime.APPLICATION])
def test_real_job_export_does_not_extend_run_lifetime(environment, lifetime):
    """The actual Job context invalidation keeps RUN grants bounded and APPLICATION explicit."""
    runtime = application(environment)
    ctx = context(runtime)
    ctx.invalidate()
    scope = RegistrationScope()
    exported = []
    def handler(invocation, _payload):
        """Publishes a selected session using the ordinary nested capability path."""
        session = invocation.observations.request(lifetime=lifetime)
        grant = invocation.observations.export(session)
        publication = invocation.capabilities.call("observation.bridge.publish@1", {"grant": grant})
        exported.append((session, grant, publication))
    runtime.capabilities.register(scope, ownerId="test-entry", capabilityId="export@1", handler=handler)
    scope.publish()
    job = runtime.runJob("export@1")
    assert job.state is JobState.SUCCEEDED
    session, grant, publication = exported[0]
    connection = runtime.invokeCapability("observation.bridge.connection@1")
    assert http(connection, publication)[0] == (410 if lifetime is ObservationLifetime.RUN else 200)
    assert grant.revoked is (lifetime is ObservationLifetime.RUN)
    session.release()


def test_foreign_authority_and_independent_app_credentials(environment):
    """Caller payload IDs never substitute for mediated same-owner authority."""
    first, second = application(environment), application(environment)
    ctx, source, grant, publication, connection = publish(first)
    foreign = context(second)
    with pytest.raises(PermissionError):
        foreign.observations.export(source)
    with pytest.raises(PermissionError):
        foreign.observations.bindReadGrant(grant)
    with pytest.raises(PermissionError):
        foreign.capabilities.call("observation.bridge.publish@1", {"grant": grant})
    with pytest.raises(ValueError):
        foreign.capabilities.call("observation.bridge.publish@1", {"grant": grant, "applicationId": foreign.identity.applicationId})
    other = second.invokeCapability("observation.bridge.connection@1")
    assert other["readCredential"] != connection["readCredential"]
    assert http(other, publication, credential=connection["readCredential"])[0] == 401
    assert http(other, publication)[0] == 404
    environment[0].closeApplicationRun(second.applicationRun.applicationRunId)
    assert http(connection, publication)[0] == 200
    ctx.invalidate()
    foreign.invalidate()


def test_independent_publications_readers_withdrawal_and_parent_revocation(environment):
    """Many readers/publications share one source and one collector while revoking independently."""
    runtime = application(environment)
    ctx, session, grant, first, connection = publish(runtime)
    second = ctx.capabilities.call("observation.bridge.publish@1", {"grant": grant})
    otherGrant = ctx.observations.export(session)
    third = ctx.capabilities.call("observation.bridge.publish@1", {"grant": otherGrant})
    claims, reads = set(environment[3]._subscriptions), environment[2].reads
    firstData = http(connection, first)[2]
    with ThreadPoolExecutor(max_workers=3) as readers:
        results = list(readers.map(lambda _index: http(connection, first)[2], range(6)))
    assert all(body == firstData for body in results)
    assert set(environment[3]._subscriptions) == claims and environment[2].reads == reads
    assert ctx.capabilities.call("observation.bridge.withdraw@1", {"publicationId": first["publicationId"]})
    assert not ctx.capabilities.call("observation.bridge.withdraw@1", {"publicationId": first["publicationId"]})
    assert http(connection, first)[0] == 404 and http(connection, second)[0] == 200
    grant.revoke()
    assert http(connection, second)[0] == 410 and http(connection, third)[0] == 200
    assert not session.snapshot().released and not otherGrant.revoked
    runtime.packLoader.close()
    assert not otherGrant.revoked and not session.snapshot().released
    assert set(environment[3]._subscriptions) == claims
    assert environment[2].closes == 0
    ctx.invalidate()
    session.release()


@pytest.mark.parametrize("case,expected", [
    ("missing", 401), ("wrong", 401), ("nonascii", 401), ("host", 403),
    ("duplicate-auth", 401), ("origin", 403),
    ("null-origin", 403), ("query", 400), ("listing", 400), ("post", 501),
])
def test_http_authorization_rejects_ambient_or_ambiguous_access(environment, capsys, case, expected):
    """Loopback itself authorizes nothing; errors/logs reveal no credentials or source evidence."""
    runtime = application(environment)
    ctx, _source, _grant, publication, connection = publish(runtime)
    kwargs = {
        "missing": {"credential": False}, "wrong": {"credential": "wrong"}, "nonascii": {"credential": "é"},
        "host": {"host": "attacker.example"}, "duplicate-host": {"extra": [("Host", "attacker.example")]},
        "duplicate-auth": {"extra": [("Authorization", "Bearer wrong")]}, "origin": {"origin": "https://evil.example"},
        "null-origin": {"origin": "null"}, "query": {"path": "/publications/" + publication["publicationId"] + "?token=" + connection["readCredential"]},
        "listing": {"path": "/publications"}, "post": {"method": "POST"},
    }[case]
    status, headers, body = http(connection, publication, **kwargs)
    assert status == expected
    assert b"hardware" not in body and connection["readCredential"].encode() not in body
    assert headers.get("Access-Control-Allow-Origin") != "*"
    captured = capsys.readouterr()
    assert connection["readCredential"] not in captured.out + captured.err
    ctx.invalidate()


def test_explicit_browser_origin_preflight_and_no_store(environment):
    """A later standalone viewer can use only its configured exact local Origin and Bearer header."""
    runtime = application(environment)
    ctx, _source, _grant, publication, connection = publish(runtime)
    status, headers, _body = http(connection, publication, credential=False, origin=ORIGIN, method="OPTIONS",
                                extra=[("Access-Control-Request-Method", "GET"), ("Access-Control-Request-Headers", "authorization")])
    assert status == 204 and headers["Access-Control-Allow-Origin"] == ORIGIN
    assert headers["Access-Control-Allow-Headers"] == "Authorization"
    assert http(connection, publication, credential=False, origin=ORIGIN)[0] == 401
    status, headers, _body = http(connection, publication, origin=ORIGIN)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert headers["Access-Control-Allow-Origin"] == ORIGIN and "Access-Control-Allow-Credentials" not in headers
    assert http(connection, publication, origin=ORIGIN, method="OPTIONS", extra=[("Access-Control-Request-Method", "POST"), ("Access-Control-Request-Headers", "authorization")])[0] == 403
    ctx.invalidate()


def test_lossless_schema_gaps_availability_no_content_and_epoch(environment):
    """Large exact times/bytes, delayed intervals, truncation and silence survive a real stream."""
    runtime = application(environment)
    ctx, source, grant, publication, connection = publish(runtime)
    clock, telemetry = environment[1], environment[3]
    scope = RegistrationScope()
    runtime.llmProviders.register(scope, ownerId="test-entry", name="fake", provider=Provider(clock, telemetry))
    scope.publish()
    result = ctx.llm.run(providerName="fake", query=LlmQuery(formatId="text/plain", payload=SECRET), observation=source)
    assert result.rawText == SECRET + "é🙂" and result.providerMetadata["private"] == SECRET
    source._hardwareError = SECRET
    original = grant.snapshot()
    status, _headers, body = http(connection, publication)
    assert status == 200 and SECRET.encode() not in body
    data = json.loads(body)
    assert data["schemaId"] == schema.SCHEMA_ID and type(data["revision"]) is str
    assert data["hardwareAvailability"] == "unavailable"
    assert int(data["hardware"][0]["collectionStartNs"]) == original.observation.hardware[0].collectionStartNs
    assert int(data["hardware"][0]["displayWallTimeNs"]) == original.observation.hardware[0].sampledTimeNs
    assert data["hardware"][0]["memory"]["totalBytes"]["value"] == str(2**56 + 1)
    assert data["hardware"][0]["swap"]["usedBytes"] == {"availability": "available", "unit": "bytes", "value": "0"}
    assert data["hardware"][0]["gpu"]["temperatureCelsius"]["availability"] == "unavailable"
    assert data["hardware"][0]["cpu"]["droppedCoreCount"] == "44"
    assert data["retention"]["hardwareMayBeTruncated"] and data["retention"]["hardwareDroppedCount"] is None
    assert int(data["stream"]["droppedWindowCount"]) == 2
    assert data["stream"]["windows"][0]["charactersPerSecond"]["value"] == 0
    assert data["correlation"][0]["hardwareIndices"] == [] and data["correlation"][0]["uncoveredIntervalsNs"]
    assert int(data["hardware"][1]["collectionEndNs"]) - int(data["hardware"][1]["collectionStartNs"]) == 17
    assert data["hardware"][1]["displayWallTimeNs"] < data["hardware"][0]["displayWallTimeNs"]
    assert data["hardware"][1]["collectionStartNs"] > data["hardware"][0]["collectionStartNs"]
    assert "providerMetadata" not in data and "tokensPerSecond" not in body.decode()
    assert http(connection, publication)[2] == body  # Unchanged terminal evidence keeps its revision.
    otherService = ObservationService()
    assert otherService._clockEpochId != data["clock"]["epochId"]
    otherService.close()
    ctx.invalidate()


def test_publication_and_source_retention_bounds(environment):
    """A finite number of selected publications caches only its latest bounded revision."""
    runtime = application(environment)
    ctx, source, grant, first, connection = publish(runtime)
    for _index in range(bridgeModule.MAX_PUBLICATIONS - 1):
        ctx.capabilities.call("observation.bridge.publish@1", {"grant": grant})
    with pytest.raises(ValueError, match="capacity"):
        ctx.capabilities.call("observation.bridge.publish@1", {"grant": grant})
    assert not grant.revoked and not source.snapshot().released
    for _index in range(5):
        environment[1].now += 2 * SECOND
        environment[3].sample()
        assert http(connection, first)[0] == 200
    assert len(source.snapshot().hardware) == 2
    publication = state(runtime)._publications[first["publicationId"]]
    assert len(publication.response) <= schema.MAX_RESPONSE_BYTES and len(publication.fingerprint) == 32
    assert publication.revision == 6
    ctx.capabilities.call("observation.bridge.withdraw@1", {"publicationId": first["publicationId"]})
    oversized = ctx.observations.request(ObservationRequest(historySamples=schema.MAX_SAMPLES + 1))
    oversizedGrant = ctx.observations.export(oversized)
    with pytest.raises(ValueError, match="retention"):
        ctx.capabilities.call("observation.bridge.publish@1", {"grant": oversizedGrant})
    assert not oversizedGrant.revoked and not oversized.snapshot().released
    ctx.invalidate()


@pytest.mark.parametrize("header", ["Host", "Origin", "Authorization"])
def test_adapter_rejects_duplicate_security_headers(environment, header):
    """Tests parsed duplicate rejection directly when local transport normalizes duplicate Host."""
    runtime = application(environment)
    bridge = state(runtime)
    handler = object.__new__(bridgeModule._Handler)
    handler.server = SimpleNamespace(bridge=bridge)
    handler.path = "/publications/" + "0" * 32
    handler.headers = HTTPMessage()
    handler.headers["Host"] = bridge._authority
    handler.headers["Origin"] = ORIGIN
    handler.headers["Authorization"] = "Bearer " + bridge.connection()["readCredential"]
    handler.headers[header] = "duplicate"
    responses = []
    def reply(status, body, *_args, **_kwargs):
        """Captures adapter rejection without a socket or any credential logging."""
        responses.append((status, body))
    handler._reply = reply
    assert handler._validate() is None
    assert responses[0][0] == (401 if header == "Authorization" else 403)


@pytest.mark.parametrize("configuration", [
    {"host": "0.0.0.0"}, {"host": "192.168.1.2"}, {"host": "localhost"},
    {"port": -1}, {"port": True}, {"allowedOrigins": ["*"]},
    {"allowedOrigins": ["null"]}, {"allowedOrigins": ["https://evil.example"]},
    {"allowedOrigins": ["http://127.0.0.1:7777/path"]},
    {"allowedOrigins": ["http://user:password@localhost:7777"]},
])
def test_actual_activation_rejects_external_or_unsafe_configuration(environment, configuration):
    """Invalid exposure policy fails real Pack activation before creating a usable endpoint."""
    with pytest.raises(ValueError):
        application(environment, config={"observationBridge": configuration})
    assert not environment[0].activeApplicationRuntimes
    assert not environment[3]._subscriptions and not environment[0]._observations._owners


@pytest.mark.parametrize("failure", ["registration", "publication"])
def test_failed_pack_activation_closes_endpoint_and_retains_no_context(environment, monkeypatch, failure):
    """Self-cleanup and actual PackLoader failure cleanup both close a started endpoint."""
    created, references = [], []
    actualBridge = bridgeModule.ObservationBridge
    actualContextInit = CodeEntryContext.__init__
    def createBridge(**kwargs):
        """Captures only test-owned endpoint handles for resource assertions."""
        bridge = actualBridge(**kwargs)
        created.append(bridge)
        return bridge
    def contextInit(ctx, **kwargs):
        """Records weak onLoad/cleanup references without retaining invocation authority."""
        actualContextInit(ctx, **kwargs)
        if ctx.identity.packId == "observation.bridge":
            references.append(weakref.ref(ctx))
    monkeypatch.setattr(bridgeModule, "ObservationBridge", createBridge)
    monkeypatch.setattr(CodeEntryContext, "__init__", contextInit)
    if failure == "registration":
        contextModule = importlib.import_module("backend.context.codeEntryContext")
        original = contextModule._CapabilityFacade.register
        def register(facade, capabilityId, handler):
            """Fails onLoad after its endpoint and first provisional capability exist."""
            if capabilityId == "observation.bridge.withdraw@1":
                raise RuntimeError("registration failed")
            return original(facade, capabilityId, handler)
        monkeypatch.setattr(contextModule._CapabilityFacade, "register", register)
    else:
        original = RegistrationScope.publish
        def publishScope(scope):
            """Fails after onLoad returned state, exercising actual onUnload cleanup."""
            if any(registration.name.startswith("observation.bridge.") for _registry, registration in scope._pending):
                raise RuntimeError("publication failed")
            return original(scope)
        monkeypatch.setattr(RegistrationScope, "publish", publishScope)
    with pytest.raises(RuntimeError, match="failed"):
        application(environment)
    assert len(created) == 1
    assert created[0]._closed and not created[0]._thread.is_alive()
    assert not created[0]._publications and created[0]._credential == ""
    gc.collect()
    assert references and all(reference() is None for reference in references)
    assert not environment[0].activeApplicationRuntimes and not environment[3]._subscriptions


def test_successful_activation_retains_no_onload_context(environment, monkeypatch):
    """Long-lived endpoint and capability closures contain Pack state, never onLoad authority."""
    references = []
    original = CodeEntryContext.__init__
    def initialize(ctx, **kwargs):
        """Retains only weak references to actual bridge invocation contexts."""
        original(ctx, **kwargs)
        if ctx.identity.packId == "observation.bridge":
            references.append(weakref.ref(ctx))
    monkeypatch.setattr(CodeEntryContext, "__init__", initialize)
    runtime = application(environment)
    runtime.invokeCapability("observation.bridge.connection@1")
    gc.collect()
    assert references and all(reference() is None for reference in references)
    assert state(runtime)._thread.is_alive()


@pytest.mark.parametrize("fault", ["thread-construction", "thread-start", "bind"])
def test_endpoint_setup_failure_releases_sockets_and_can_reactivate(environment, monkeypatch, fault):
    """Real socket construction followed by startup failure leaves no endpoint or observation claims."""
    servers = []
    originalServer = bridgeModule._Server
    originalThread = bridgeModule.Thread
    def serverFactory(*args, **kwargs):
        """Retains actual bound sockets so cleanup is verified after thread startup failure."""
        server = originalServer(*args, **kwargs)
        servers.append(server)
        return server
    def failStart():
        """Injects a thread start failure after its endpoint socket was bound."""
        raise OSError("thread start failed")
    def threadFactory(*args, **kwargs):
        """Injects construction/start failure without replacing socket cleanup behavior."""
        if fault == "thread-construction":
            raise OSError("thread construction failed")
        thread = originalThread(*args, **kwargs)
        thread.start = failStart
        return thread
    if fault == "bind":
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with pytest.raises(OSError):
                application(environment, config={"observationBridge": {"port": occupied.getsockname()[1]}})
    else:
        with monkeypatch.context() as faultPatch:
            faultPatch.setattr(bridgeModule, "_Server", serverFactory)
            faultPatch.setattr(bridgeModule, "Thread", threadFactory)
            with pytest.raises(OSError):
                application(environment)
        assert servers and all(server.socket.fileno() == -1 for server in servers)
    assert not environment[3]._subscriptions
    runtime = application(environment)
    assert runtime.invokeCapability("observation.bridge.connection@1")["endpoint"]


def test_failed_unload_still_withdraws_evidence_and_keeps_source_owner(environment, monkeypatch):
    """A cleanup exception is reported after serving/access stopped; source/parent grants survive."""
    runtime = application(environment)
    ctx, source, grant, _publication, _connection = publish(runtime)
    bridge = state(runtime)
    originalClose = bridge._server.server_close
    def failClose():
        """Models socket cleanup reporting an error after actually closing the socket."""
        originalClose()
        raise OSError("socket cleanup failed")
    monkeypatch.setattr(bridge._server, "server_close", failClose)
    with pytest.raises(ExceptionGroup):
        runtime.packLoader.close()
    assert bridge._closed and not bridge._thread.is_alive() and not bridge._publications
    assert bridge._credential == "" and not grant.revoked and not source.snapshot().released
    ctx.invalidate()


@pytest.mark.parametrize("operation", ["withdraw", "close"])
def test_withdrawal_serializes_with_inflight_response_and_clears_cache(environment, operation):
    """When withdrawal returns, no blocked earlier writer or cached publication can still be served."""
    runtime = application(environment)
    ctx, source, grant, publication, connection = publish(runtime)
    bridge = state(runtime)
    entered, resume, revoking = Event(), Event(), Event()
    delivered = []
    def writer(status, body):
        """Holds an actual registry response under its publication/send lock."""
        entered.set()
        assert resume.wait(5)
        delivered.append((status, body))
    def revoke():
        """Attempts independently authorized withdrawal while the prior response is in flight."""
        revoking.set()
        return bridge.withdraw(publication["publicationId"]) if operation == "withdraw" else bridge.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(bridge.serve, publication["publicationId"], writer)
        assert entered.wait(5)
        withdrawing = pool.submit(revoke)
        assert revoking.wait(5) and not withdrawing.done()
        resume.set()
        sending.result(5)
        withdrawing.result(5)
    assert delivered[0][0] == 200 and not bridge._publications
    assert not grant.revoked and not source.snapshot().released
    if operation == "withdraw":
        assert http(connection, publication)[0] == 404
    else:
        assert not bridge._thread.is_alive()
    ctx.invalidate()


def test_source_revocation_orders_against_inflight_grant_read(environment, monkeypatch):
    """Source release waits for the earlier authorized read and prevents all subsequent reads."""
    runtime = application(environment)
    ctx, source, grant, publication, connection = publish(runtime)
    entered, resume, revoking = Event(), Event(), Event()
    original = source.snapshot
    def snapshot():
        """Blocks only the underlying read, leaving real grant/source locking in place."""
        entered.set()
        assert resume.wait(5)
        return original()
    monkeypatch.setattr(source, "snapshot", snapshot)
    def revoke():
        """Revokes source authority while the other reader owns the lifecycle lock."""
        revoking.set()
        source.release()
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(grant.snapshot)
        assert entered.wait(5)
        releasing = pool.submit(revoke)
        assert revoking.wait(5) and not releasing.done()
        resume.set()
        evidence = reading.result(5)
        releasing.result(5)
    assert evidence.observation.hardware
    assert grant.revoked and http(connection, publication)[0] == 410
    ctx.invalidate()


def test_shutdown_interrupts_partial_http_headers(environment, monkeypatch):
    """A slow reader cannot hold Pack unload open by leaving HTTP headers incomplete."""
    runtime = application(environment)
    bridge = state(runtime)
    accepted = Event()
    original = bridge._server.get_request
    def accept():
        """Signals after a real connection has entered the endpoint's active socket set."""
        result = original()
        accepted.set()
        return result
    monkeypatch.setattr(bridge._server, "get_request", accept)
    endpoint = urlsplit(bridge.connection()["endpoint"])
    with socket.create_connection((endpoint.hostname, endpoint.port), timeout=3) as client:
        client.sendall(b"GET /publications/unfinished HTTP/1.1\r\nHost: ")
        assert accepted.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(runtime.packLoader.close).result(5)
    assert not bridge._thread.is_alive() and not bridge._server._clients


def test_application_owner_teardown_revokes_grants_and_http(environment):
    """Real host application close revokes source authority then closes the actual Pack endpoint."""
    runtime = application(environment)
    ctx, _source, grant, _publication, _connection = publish(runtime)
    bridge = state(runtime)
    environment[0].closeApplicationRun(runtime.applicationRun.applicationRunId)
    assert grant.revoked and bridge._closed and not bridge._publications
    assert not bridge._thread.is_alive() and not environment[3]._subscriptions
    ctx.invalidate()


def test_revoked_or_malformed_exports_and_grants_are_rejected(environment):
    """New authority cannot be acquired from an expired source or a fabricated payload shape."""
    runtime = application(environment)
    ctx, source, grant, _publication, _connection = publish(runtime)
    with pytest.raises(TypeError):
        ctx.observations.bindReadGrant({"session": source})
    with pytest.raises(PermissionError):
        ctx.observations.export({"session": source})
    grant.revoke()
    grant.revoke()
    with pytest.raises(ObservationGrantRevoked):
        ctx.observations.bindReadGrant(grant)
    source.release()
    with pytest.raises(PermissionError):
        ctx.observations.export(source)
    assert not environment[3]._subscriptions
    ctx.invalidate()


def test_no_cached_fallback_on_optional_read_failure_and_recovery(environment, monkeypatch):
    """An optional read/serialization fault returns fixed unavailability, never yesterday's cache."""
    runtime = application(environment)
    ctx, source, grant, publication, connection = publish(runtime)
    saved = http(connection, publication)[2]
    def fail():
        """Raises a private source diagnostic that must never appear in HTTP responses."""
        raise OSError(SECRET)
    with monkeypatch.context() as fault:
        fault.setattr(source, "snapshot", fail)
        status, _headers, body = http(connection, publication)
        assert status == 503 and SECRET.encode() not in body
        assert state(runtime)._publications[publication["publicationId"]].response is None
    status, _headers, body = http(connection, publication)
    assert status == 200
    assert json.loads(body)["sessionId"] == json.loads(saved)["sessionId"]
    assert not grant.revoked and not source.snapshot().released
    ctx.invalidate()


def test_snapshot_json_bounds_nonfinite_values_and_unknown_timing(environment):
    """The transport-independent serializer preserves exact counts/null time without leaking error text."""
    runtime = application(environment)
    ctx, source, grant, publication, _connection = publish(runtime)
    # Start/finalize the accepted PM-02 handle, then use immutable synthetic evidence.
    claim = source._stream.begin()
    claim.recordText("text")
    from backend.llm.streamObservation import StreamOutcome
    claim.finish(StreamOutcome.COMPLETED)
    evidence = grant.snapshot()
    stream = evidence.observation.stream
    window = replace(stream.windows[0], gapCount=2**60 + 5, gapSumNs=2**62 + 7)
    stream = replace(stream, characterCount=2**65 + 11, windows=(window,), droppedWindowCount=2**56 + 13)
    sample = evidence.observation.hardware[0]
    sample = replace(sample, collectionStartNs=None, collectionEndNs=None,
                     cpuUtilization=CpuUtilizationObservation(float("nan"), (float("inf"),)))
    changed = replace(evidence, observation=replace(evidence.observation, hardware=(sample,), stream=stream, windows=()))
    data = json.loads(schema.serialize(changed, publication["publicationId"], 2**63 + 1))
    assert data["revision"] == str(2**63 + 1)
    assert data["stream"]["characterCount"] == str(2**65 + 11)
    assert data["stream"]["windows"][0]["gapSumNs"] == str(2**62 + 7)
    assert data["hardware"][0]["collectionStartNs"] is None
    assert data["hardware"][0]["cpu"]["aggregatePercent"]["availability"] == "unavailable"
    assert data["hardware"][0]["cpu"]["perLogicalCorePercent"][0]["value"] is None
    assert data["clock"]["epochId"] == evidence.clockEpochId
    ctx.invalidate()


def test_activated_pack_does_not_attach_to_unobserved_inference(environment, monkeypatch):
    """Executing an ordinary unobserved call after activation allocates no measurement or collector."""
    runtime = application(environment)
    ctx = context(runtime)
    class UnobservedProvider(Provider):
        """Normal provider without test-owned manual hardware sampling."""

        def stream(self, request):
            """Returns content normally without any observation-specific action."""
            yield LlmStreamEvent(eventType="delta", text=SECRET)
            yield LlmStreamEvent(eventType="completed")
    scope = RegistrationScope()
    runtime.llmProviders.register(scope, ownerId="test-entry", name="fake", provider=UnobservedProvider(environment[1], environment[3]))
    scope.publish()
    def forbidden(*_args, **_kwargs):
        """Rejects any implicit stream observation construction on the disabled path."""
        raise AssertionError("Unexpected monitoring allocation")
    monkeypatch.setattr("backend.observation.StreamObservation", forbidden)
    result = ctx.llm.run(providerName="fake", query=LlmQuery(formatId="text/plain", payload=SECRET))
    assert result.rawText == SECRET
    assert not runtime._observationOwner._sessions and not state(runtime)._publications
    assert not environment[3]._subscriptions and environment[2].reads == 0
    ctx.invalidate()


def test_pack_resolves_sibling_helpers_from_copied_root(environment, tmp_path):
    """Optional Pack loading uses its own resolved source bundle, including outside first-party."""
    host = environment[0]
    copied = tmp_path / "installed-observation-pack"
    shutil.copytree(PACK_ROOT, copied, ignore=shutil.ignore_patterns("__pycache__"))
    resolver = PackResolver(roots=(copied,))
    runtime = application(environment, bridge=False)
    runtime.packLoader.activatePack(resolver.requireSingle("observation.bridge"))
    bridge = state(runtime)
    assert type(bridge).__module__ != bridgeModule.__name__
    assert runtime.invokeCapability("observation.bridge.connection@1")["endpoint"].startswith("http://127.0.0.1:")
    assert not host._observations._owners.difference({runtime._observationOwner})
    assert not environment[3]._subscriptions
    runtime.packLoader.close()
    assert not bridge._thread.is_alive()
