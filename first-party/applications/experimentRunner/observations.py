# file: first-party/applications/experimentRunner/observations.py ; version: 2
"""Explicit optional PM/G composition with in-flight source retention and private handoff."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from threading import Event, Lock, Thread
import time
from urllib.parse import urlsplit
from uuid import uuid4

from backend.observation import ObservationLifetime, ObservationRequest

ofmt = importlib.import_module("first-party.applications.experimentRunner.observationFormat")
fmt = ofmt.fmt
codec = importlib.import_module("first-party.observationBridge.schema")
bridge = importlib.import_module("first-party.observationBridge.bridge")


def defaults():
    """Return a complete explicitly disabled host4 policy with bounded inert defaults."""
    return dict(enabled=False, calls=[], request=dict(memory=False, cpu=False, gpu=False, stream=True,
        sampleIntervalSeconds=1, windowSeconds=1, historySamples=32, historyWindows=60),
        live=dict(enabled=False, call=None, allowedOrigin=None, maxLifetimeSeconds=10))


def validate(value, definition=None):
    """Validate trusted declarative selection before launch; definitions cannot request collectors."""
    fmt.fields(value, ("enabled", "calls", "request", "live"))
    fmt.require(type(value["enabled"]) is bool and type(value["calls"]) is list and len(value["calls"]) <= 8)
    calls = [ofmt.call(call) for call in value["calls"]]
    fmt.require(len(calls) == len(set(calls)) and (value["enabled"] or not calls))
    ofmt.request(value["request"])
    ObservationRequest(**value["request"])
    live = value["live"]
    fmt.fields(live, ("enabled", "call", "allowedOrigin", "maxLifetimeSeconds"))
    fmt.require(type(live["enabled"]) is bool)
    ofmt.number(live["maxLifetimeSeconds"], .25, 30)
    if live["enabled"]:
        fmt.require(ofmt.call(live["call"]) in calls)
        bridge.validateOrigins([live["allowedOrigin"]])
        parsed = urlsplit(live["allowedOrigin"])
        fmt.require(parsed.hostname == "127.0.0.1" and 1024 <= parsed.port <= 65535)
    else:
        fmt.require(live["call"] is None and live["allowedOrigin"] is None)
    if definition is not None:
        valueDefinition = definition.value()
        turnIds = ([s["id"] for s in valueDefinition["procedure"] if s["op"] == "inference"]
            if valueDefinition["schemaId"] == "actant.experiment.definition@3"
            else [t["id"] for t in valueDefinition["conversation"]["turns"]])
        for config, repetition, turn in calls:
            fmt.require(config <= len(valueDefinition["configurations"]) and repetition <= valueDefinition["repetitions"]
                        and turn in turnIds)


def archivePolicy(value, effective):
    """Project only explicit needs/selection/bounds and closed effective live availability."""
    live = value["live"]
    result = dict(schemaId=fmt.PREFIX + "observation-policy@1", enabled=value["enabled"], calls=value["calls"], request=value["request"],
        liveSelection=dict(requested=live["enabled"], call=live["call"], endpointPolicy="loopback-ephemeral",
            originPolicy="exact-loopback", maxLifetimeSeconds=live["maxLifetimeSeconds"]),
        retention=dict(maxSnapshots=9, maxBytes=32 * 1024 * 1024, deadlineOrigin="before-session-request",
            expiryPolicy="viewer-only-in-flight-then-source-tail"), effectiveLiveAvailability=effective)
    ofmt.policy(result)
    return result


@dataclass(frozen=True, slots=True, repr=False)
class PrivateNotice:
    """Bounded transient private bytes; repr never includes endpoint or bearer credential."""

    data: bytes = field(repr=False)

    def value(self):
        """Give only the explicitly trusted callback a detached private connection notice."""
        return fmt.decode(self.data)

    def __repr__(self):
        """Keep incidental diagnostics free of credentials, paths and descriptors."""
        return "<PrivateNotice>"


class Controller:
    """Own observation-only handles and at most one private callback worker, never Context."""

    def __init__(self, policy, handoff=None):
        """Retain trusted host hook; no work, listener or collector starts here."""
        self.policy, self.handoff = policy, handoff
        self.effective = "not-requested" if not policy["live"]["enabled"] else "no-private-handoff" if handoff is None else "pending-activation"
        self.calls = []
        self.worker = None
        self.handoffState = "not-requested"
        self.handoffLock = Lock()

    def begin(self, ctx, port, turnId):
        """Prepare one guarded selected call, otherwise allocate no observation session."""
        selected = dict(configurationIndex=port.configurationIndex, repetition=port.repetition, turnId=turnId)
        if not self.policy["enabled"] or selected not in self.policy["calls"]:
            return None
        call = Call(self, port, selected)
        self.calls.append(call)
        try:
            call.setup(ctx)
        except BaseException as error:
            call.__exit__(type(error), error, None)
            raise
        return call

    def handOff(self, value):
        """Dispatch one bounded notice outside all runtime/controller/queue locks, without waiting."""
        raw = fmt.canonical(value)
        fmt.require(len(raw) <= 4096)
        notice = PrivateNotice(raw)
        def deliver():
            """Consume exactly once; no raw error object/trace/cause enters diagnostics."""
            state = "completed"
            try:
                self.handoff(notice)
            except BaseException:
                state = "failed"
            with self.handoffLock:
                self.handoffState = state
        self.worker = Thread(target=deliver, name="experiment-private-handoff", daemon=True)
        self.handoffState = "pending"
        try:
            self.worker.start()
        except Exception:
            self.handoffState = "failed"
            self.worker = None
            raise

    def finish(self, runtime):
        """Request tail release and withdraw at a fresh host-owned App boundary before seal."""
        primary = None
        for call in self.calls:
            try:
                call.expire()
            except BaseException as error:
                call.error("tail")
                if not isinstance(error, Exception) and primary is None:
                    primary = error
            if call.timer is not None:
                call.timer.join(.25)
            if call.publicationId is not None:
                try:
                    runtime.invokeCapability("observation.bridge.withdraw@1", {"publicationId": call.publicationId})
                    call.publicationWithdraw = "completed"
                except BaseException as error:
                    call.publicationWithdraw = "failed"
                    call.error("withdraw")
                    if not isinstance(error, Exception) and primary is None:
                        primary = error
            if call.live:
                call.writeSnapshot("lifetime-final", call.tail)
            value = dict(schemaId=fmt.PREFIX + "observation-cleanup@1", observationId=call.observationId,
                sessionRelease=call.sessionRelease, parentRevoke=call.parentRevoke, publicationWithdraw=call.publicationWithdraw,
                handoffWorkerClose=self.handoffState if call.live else "not-requested", errors=list(call.errors))
            ofmt.cleanup(value)
            call.port.observationRecord("observation-cleanup", call.observationId, value)
        if primary is not None:
            raise primary


class Call:
    """One in-flight source guard; viewer deadline never releases attached inference evidence."""

    def __init__(self, controller, port, selected):
        """Acquire guard before any setup or timer visibility; deadline starts before request."""
        self.controller, self.port, self.selected = controller, port, selected
        self.observationId = uuid4().hex
        self.live = controller.effective == "available" and controller.policy["live"]["call"] == selected
        self.lifetime = "application" if self.live else "run"
        self.deadline = time.monotonic() + controller.policy["live"]["maxLifetimeSeconds"]
        self.lock = Lock()
        self.active, self.expired, self.releasing = True, False, False
        self.session = self.capture = self.viewer = None
        self.timer = None
        self.stop = Event()
        self.publicationId = None
        self.tail = None
        self.errors = []
        self.sessionRelease = self.parentRevoke = self.publicationWithdraw = "not-requested"

    def error(self, stage, category="failed"):
        """Retain only fixed bounded independent observation cleanup diagnostics."""
        with self.lock:
            if len(self.errors) < 8:
                self.errors.append(dict(stage=stage, category=category))

    def setup(self, ctx):
        """Request same-owner observation once; optional setup failure chooses one unobserved call."""
        try:
            self.session = ctx.observations.request(ObservationRequest(**self.controller.policy["request"]),
                lifetime=ObservationLifetime.APPLICATION if self.live else ObservationLifetime.RUN)
            self.sessionRelease = "pending"
            self.capture = ctx.observations.export(self.session)
        except Exception:
            self.error("setup")
            return
        if self.live:
            try:
                self.viewer = ctx.observations.export(self.session)
                publication = ctx.capabilities.call("observation.bridge.publish@1", {"grant": self.viewer})
                self.publicationId = publication["publicationId"]
                self.publicationWithdraw = "pending"
                connection = ctx.capabilities.call("observation.bridge.connection@1")
                self.controller.handOff(dict(schemaId=fmt.PREFIX + "private-observation-handoff@1",
                    suiteId=self.port.writer.header["suiteId"], unitId=self.port.unitId, turnId=self.port.turnId,
                    observationId=self.observationId, connection=connection, publication=publication))
            except Exception:
                self.error("handoff")
            self.timer = Thread(target=self.waitExpiry, name="experiment-observation-expiry", daemon=True)
            try:
                self.timer.start()
            except Exception:
                self.timer = None
                self.error("setup")
                self.expire()

    def waitExpiry(self):
        """Wait only for this selected source's finite viewer/post-call deadline."""
        self.stop.wait(max(0, self.deadline - time.monotonic()))
        try:
            self.expire()
        except BaseException as error:
            self.error("tail", "failed" if isinstance(error, Exception) else "interrupted")
            if self.sessionRelease == "pending":
                self.sessionRelease = "failed"

    def expire(self):
        """Mark expiry under a short lock; revoke viewer and release idle source outside it."""
        with self.lock:
            self.expired = True
            active = self.active
        self.stop.set()
        if self.viewer is not None:
            try:
                self.viewer.revoke()
            except Exception:
                self.error("viewer-revoke")
        if not active:
            self.release()

    def snapshot(self):
        """Copy only public immutable data using the online shared allowlist serializer."""
        if self.capture is None:
            return None
        try:
            raw = codec.serialize(self.capture.snapshot(), "app-codec-projection", 0)
            data = fmt.decode(raw)
            del data["publicationId"], data["revision"]
            ofmt.publicData(data)
            return data
        except Exception:
            self.error("snapshot")
            return None

    def writeSnapshot(self, phase, data):
        """ACK a typed optional observation; actual archive write failures stay fatal."""
        value = dict(schemaId=fmt.PREFIX + "observation@1", observationId=self.observationId,
            suiteId=self.port.writer.header["suiteId"], unitId=self.port.unitId, turnId=self.selected["turnId"],
            attemptId=self.port.attemptIdFor(self.selected["turnId"]), providerCallId=self.port.callIdFor(self.selected["turnId"]),
            phase=phase, availability="available" if data is not None else "unavailable",
            reason="captured" if data is not None else "setup-unavailable" if self.capture is None else "snapshot-unavailable",
            measurementDefinition=ofmt.MEASUREMENT, clockEpochId=None if data is None else data["clock"]["epochId"],
            sessionId=None if data is None else data["sessionId"], sourceLifetime=self.lifetime, publicData=data)
        if data is not None and len(fmt.canonical(value)) > min(2 * 1024 * 1024, self.port.writer.caps["artifactBytes"]):
            value.update(availability="unavailable", reason="over-limit", clockEpochId=None, sessionId=None, publicData=None)
        ofmt.observation(value)
        self.port.observationRecord("observation-snapshot", self.observationId, value)

    def release(self):
        """Copy bounded tail before release; never release an in-flight or already owned source."""
        with self.lock:
            if self.active or self.releasing:
                return
            self.releasing = True
        try:
            if self.live:
                self.tail = self.snapshot()
            for grant in (self.viewer, self.capture):
                if grant is not None:
                    grant.revoke()
            self.parentRevoke = "completed" if self.capture is not None else "not-requested"
        except Exception:
            self.parentRevoke = "failed"
            self.error("viewer-revoke")
        finally:
            if self.session is not None:
                try:
                    self.session.release()
                    self.sessionRelease = "completed"
                except Exception:
                    self.sessionRelease = "failed"
                    self.error("source-release")

    def __enter__(self):
        """Keep guarded setup through actual one-use facade attachment and immutable final ACK."""
        return self.session if self.capture is not None else None

    def __exit__(self, kind, error, traceback):
        """Preserve first inference exit while independent snapshot/archive/release cleanup runs."""
        secondary = None
        try:
            self.writeSnapshot("call-final", self.snapshot())
        except BaseException as failure:
            self.port.writer.fail(failure)
            secondary = failure
        finally:
            with self.lock:
                self.active = False
                release = self.expired or not self.live or time.monotonic() >= self.deadline
            if release:
                try:
                    self.release()
                except BaseException as failure:
                    self.error("source-release")
                    if secondary is None:
                        secondary = failure
        if error is None and secondary is not None:
            raise secondary
        return False
