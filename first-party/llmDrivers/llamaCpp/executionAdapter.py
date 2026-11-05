# file: first-party/llmDrivers/llamaCpp/executionAdapter.py ; version: 4
"""Trusted llama.cpp adoption of ER-01 residency and ER-02 invocation sessions.

Stock HTTP cannot require physical cache reuse. Retention and required freshness
reject; ordinary freshness requests disable prompt reuse without hidden-state claims.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, RLock
from weakref import WeakKeyDictionary
import urllib.request as http

from backend.llm.errors import LlmExecutionOwnershipError, LlmProviderProtocolError, LlmProviderConnectionError
from backend.llm.executionSessions import LlmOwnerGrant, LlmContextEffect, LlmSessionCleanup, LlmSessionExit
from backend.llm.llmTypes import LlmExecutionProfile
from backend.llm.structuredMessages import LlmMessages, LLM_MESSAGES_FORMAT_ID
from backend.runtime.executionResources import (
    ResidencyDescriptor, Materialization, ProviderEffect, EffectStatus, Quiescence,
    ResourceConflict, ResourceState,
)


def _implementation():
    """Use the same substrate authority/table across dynamically loaded facades."""
    name = "actantFirstPartyLlamaCppImplementation"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("codeEntry.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


_impl = _implementation()
_transportName = 'actantFirstPartyLlamaCppOwnedTransport'
if _transportName not in sys.modules:
    _transportSpec = importlib.util.spec_from_file_location(
        _transportName, Path(__file__).with_name('ownedTransport.py'))
    _transportModule = importlib.util.module_from_spec(_transportSpec)
    sys.modules[_transportName] = _transportModule
    try:
        _transportSpec.loader.exec_module(_transportModule)
    except BaseException:
        sys.modules.pop(_transportName, None)
        raise
_transport = sys.modules[_transportName]
_evidenceName = 'actantFirstPartyLlamaCppRequestEvidence'
if _evidenceName not in sys.modules:
    _evidenceSpec = importlib.util.spec_from_file_location(
        _evidenceName, Path(__file__).with_name('requestEvidence.py'))
    _evidenceModule = importlib.util.module_from_spec(_evidenceSpec)
    sys.modules[_evidenceName] = _evidenceModule
    try:
        _evidenceSpec.loader.exec_module(_evidenceModule)
    except BaseException:
        sys.modules.pop(_evidenceName, None)
        raise
_evidence = sys.modules[_evidenceName]
PrivateRequestEvidence = _evidence.PrivateRequestEvidence
_OK = ProviderEffect(EffectStatus.ACKNOWLEDGED, Quiescence.CONFIRMED)
_UNKNOWN = ProviderEffect(EffectStatus.UNKNOWN, Quiescence.UNKNOWN)
_EMPTY_FAILURE = ProviderEffect(EffectStatus.FAILED, Quiescence.CONFIRMED)
# Root the table in the canonical substrate, not in each import alias of this file.
if not hasattr(_impl, "_executionDomainTable"):
    _impl._executionDomainTable = (RLock(), WeakKeyDictionary())
_tableLane, _domains = _impl._executionDomainTable


@dataclass(frozen=True, slots=True)
class EntrySelector:
    """Exact host-approved CodeEntry source identity, never an owner ID assertion."""

    packId: str
    packVersion: str
    codeEntryId: str
    sourceSha256: str

    @classmethod
    def fromSource(cls, packId, packVersion, codeEntryId, sourcePath):
        """Hash explicitly host-selected bytes; transitive imports are not attested."""
        return cls(packId, packVersion, codeEntryId, hashlib.sha256(Path(sourcePath).read_bytes()).hexdigest())

    def matches(self, identity):
        """Select the actual loaded incarnation, not a caller-supplied identity string."""
        return (identity.packId, identity.packVersion, identity.codeEntryId, identity.sourceSha256) == (
            self.packId, self.packVersion, self.codeEntryId, self.sourceSha256)


@dataclass(frozen=True, slots=True)
class LlamaCppGrant:
    """Immutable initial-activation selector grant for one exact caller/helper."""

    entry: EntrySelector
    selectors: tuple
    allowTransition: bool = False


@dataclass(frozen=True, slots=True)
class RequestEvidencePolicy:
    """Host-selected capture policy; omitted callers remain capture-disabled."""

    entry: EntrySelector
    required: bool = True
    maxBodyBytes: int = 2 * 1024 * 1024

    def __post_init__(self):
        """Validate finite capture bounds independently of provider options."""
        if (type(self.entry) is not EntrySelector or type(self.required) is not bool or
                type(self.maxBodyBytes) is not int or not 1 <= self.maxBodyBytes <= 8 * 1024 * 1024):
            raise ValueError("Invalid private request evidence policy.")


@dataclass(frozen=True, slots=True)
class _Profile:
    """Private frozen launch inputs; a model label is separate from compatibility."""

    prepared: object = field(repr=False)
    model: object = field(repr=False)
    identity: bytes
    executableObservation: object = field(repr=False)


class LlamaCppCatalog:
    """Host-authored frozen LC launch catalog, with no process or inference startup."""

    def __init__(self, config):
        """Validate LC inputs once and capture relevant environment/cwd including absence."""
        self.prepared = _impl._configuration.prepare(config)
        driver = _impl.LlamaCppDriver(self.prepared)
        if not driver.manageServer:
            raise ValueError("A controlled llama.cpp catalog requires managed mode.")
        self.values = self.prepared.values
        self.envelope = bytes.fromhex(_impl._configuration.identity({
            "adapter": "actant.llamaCpp.execution@1", "source": _impl._configuration.SOURCE_COMMIT,
            "executable": str(driver.executable), "host": driver.host, "port": driver.port,
            "baseUrl": driver.baseUrl}))
        profiles = {}
        observed = _impl._configuration.freeze(_impl._configuration.executableIdentity(driver.executable))
        for name, model in driver.models.items():
            identity = _impl._configuration.identity({"argv": driver._serverArgs(model),
                "environment": self.prepared.environment, "cwd": self.prepared.workingDirectory,
                "automaticConfig": self.prepared.automaticConfig,
                "executableObservation": observed,
                "timeout": driver.startupTimeoutSeconds, "capabilityCheck": driver.capabilityCheck})
            profiles[name] = _Profile(self.prepared, model, bytes.fromhex(identity), observed)
        profiles[None] = profiles[driver.defaultModel]
        from types import MappingProxyType
        self.profiles = MappingProxyType(profiles)

    @classmethod
    def fromConfig(cls, config):
        """Create trusted catalog input; runtime Pack JSON cannot install it."""
        return cls(config)


class LlamaCppComposition:
    """Host-selected provider composition; grants are snapshots, never subscriptions."""

    def __init__(self, *, domainId, catalog, providerEntries, grants,
                 requestEvidence=None, evidencePolicies=()):
        """Record explicit host authority independently of mutable Application config."""
        if not isinstance(catalog, LlamaCppCatalog):
            raise TypeError("Expected a trusted LlamaCppCatalog.")
        if type(domainId) is not str or not domainId:
            raise ValueError("Controlled domain ID must be nonempty.")
        self.domainId, self.catalog = domainId, catalog
        self.providerEntries, self.grants = tuple(providerEntries), tuple(grants)
        self.requestEvidence, self.evidencePolicies = requestEvidence, tuple(evidencePolicies)
        if (requestEvidence is not None and type(requestEvidence) is not PrivateRequestEvidence or
                any(type(p) is not RequestEvidencePolicy for p in self.evidencePolicies) or
                (self.evidencePolicies and requestEvidence is None)):
            raise ValueError("Invalid private request evidence composition.")

    def configurationEvidence(self, resources, model=None):
        """Explicit private host accessor; never project raw launch inputs to telemetry.

        A matching live residency supplies its actual immutable launch snapshot.
        Otherwise return planned/unlaunched evidence for this trusted catalog.
        This read-only accessor conveys no invocation or process-control authority.
        """
        profile = self.catalog.profiles[model]
        with _tableLane:
            owner = _domains.get(resources, {}).get(self.domainId)
            resident = None if owner is None else owner.resident
        if resident is not None and resident[0].identity == profile.identity:
            return resident[1].configurationEvidence(resident[2].name)
        driver = _impl.LlamaCppDriver(profile.prepared)
        return driver.configurationEvidence(profile.model.name)

    def compose(self, snapshot):
        """Bind one exact registration, actual owner set and owned lifetime ticket."""
        snapshot.ticket.requireCurrent()
        identities = {identity.codeEntryInstanceId: identity for identity, _ in snapshot.owners}
        registrations = [r for r in snapshot.registrations if r.name == "llama.cpp" and
            r.ownerId in identities and any(s.matches(identities[r.ownerId]) for s in self.providerEntries)]
        if len(registrations) != 1:
            raise LlmExecutionOwnershipError("Trusted llama.cpp provider identity is missing or ambiguous.")
        registration = registrations[0]
        try:
            existing = snapshot.bindings.binding(snapshot.registry, registration)
        except LlmExecutionOwnershipError:
            # Replacement intent is fail-closed. Only a fresh explicit trusted bind
            # below can publish; accepted R1 checks still validate exact lifetime.
            existing = None
        facade = registration.value
        if type(facade) is not _impl._ManagedFacade or facade.requestedConfiguration != self.catalog.values:
            raise LlmExecutionOwnershipError("Managed llama.cpp configuration or facade is not host-approved.")
        captureOwners = {}
        for policy in self.evidencePolicies:
            matches = [owner for identity, owner in snapshot.owners if policy.entry.matches(identity)]
            if len(matches) != 1 or matches[0] in captureOwners:
                raise LlmExecutionOwnershipError("Private request evidence caller is missing or ambiguous.")
            captureOwners[matches[0]] = (policy.required, policy.maxBodyBytes)
        if existing is not None:
            existing.requireLive()
            with _tableLane:
                owner = _domains.get(snapshot.resources, {}).get(self.domainId)
                if owner is None or owner.envelope != self.catalog.envelope or existing.domain is not owner.domain:
                    raise LlmExecutionOwnershipError("Controlled llama.cpp domain envelope conflicts.")
            if (existing.adapter.captureBroker is not self.requestEvidence or
                    existing.adapter.captureOwners != captureOwners):
                raise LlmExecutionOwnershipError("Private request evidence binding policy conflicts.")
            return  # No grant extension, including a matching later incarnation.
        grants = {}
        for policy in self.grants:
            matches = [owner for identity, owner in snapshot.owners if policy.entry.matches(identity)]
            if len(matches) != 1:
                raise LlmExecutionOwnershipError("Trusted llama.cpp caller identity is missing or ambiguous.")
            if matches[0] in grants:
                raise LlmExecutionOwnershipError("Duplicate caller grant authority.")
            grants[matches[0]] = LlmOwnerGrant(policy.selectors, policy.allowTransition)
        # Never take over a live legacy SharedServiceRegistry driver.
        from backend.runtime.sharedServices import sharedServicesForApplicationRun
        try:
            legacy = sharedServicesForApplicationRun(identities[registration.ownerId].applicationRunId)
        except RuntimeError:
            legacy = None
        if legacy is not None:
            with legacy._lane:
                if "llm.driver.llama.cpp.managed" in legacy._records:
                    raise LlmExecutionOwnershipError("A live legacy managed llama.cpp lease prevents migration.")
        with _tableLane:
            table = _domains.setdefault(snapshot.resources, {})
            owner = table.get(self.domainId)
            if owner is None:
                owner = _DomainOwner(snapshot.resources, self.domainId, self.catalog.envelope)
                table[self.domainId] = owner
            if owner.envelope != self.catalog.envelope:
                raise LlmExecutionOwnershipError("Controlled llama.cpp domain envelope conflicts.")
            profiles = {selector: owner.profile(profile) for selector, profile in self.catalog.profiles.items()}
            lease = owner.lease()
        try:
            snapshot.ticket.requireCurrent()
            snapshot.bindings.bind(snapshot.registry, registration, domain=owner.domain,
                profiles=profiles, adapter=_ExecutionAdapter(owner, facade,
                    self.requestEvidence, captureOwners), grants=grants, leaseCloser=lease.close)
        except BaseException as primary:
            if not getattr(primary, "llmBindingPublished", False):
                try:
                    lease.close()
                except Exception as cleanup:
                    primary.llmCompositionCleanup = cleanup
            raise


class _Lease:
    """Binding lifetime only; ER-01 owns all claims, pins and transition authority."""

    def __init__(self, owner):
        """Keep the exact stable adapter alive through broker-owned cleanup."""
        self.owner, self.closed = owner, False

    def close(self):
        """Final drained ticket unloads via the host cleanup participant exactly once."""
        owner = self.owner
        with owner.lane:
            if self.closed:
                return
            if len(owner.leases) > 1:
                owner.leases.remove(self)
                self.closed = True
                return
            owner.closing = True
        if owner.domain.snapshot.state is not ResourceState.CLOSED:
            result = owner.cleanup.unloadIdle()
            if isinstance(result, ResourceConflict) or result.snapshot.state not in (ResourceState.EMPTY, ResourceState.CLOSED):
                raise LlmProviderConnectionError("Managed llama.cpp final release is incomplete.")
        with owner.lane:
            owner.leases.discard(self)
            self.closed = True
            owner.closing = False


class _DomainOwner:
    """Stable domain adapter; loaded drivers are private per-residency objects."""

    def __init__(self, resources, domainId, envelope):
        """Register one stable envelope without loading a model or probing a binary."""
        self.envelope, self.lane = envelope, RLock()
        self.references, self.leases = {}, set()
        self.closing = False
        self.resident = None
        self.slots = set()
        self.domain = resources.registerDomain(domainId, compatibilityKey=envelope, adapter=self)
        self.cleanup = self.domain.openParticipant(allowedProfiles=(), allowIdleTransition=True)

    def profile(self, profile):
        """Deduplicate exact descriptors while allowing new trusted catalog profiles."""
        if profile.identity not in self.references:
            reference = self.domain.registerProfile(ResidencyDescriptor(profile.identity, profile))
            if isinstance(reference, ResourceConflict):
                raise LlmExecutionOwnershipError("Controlled llama.cpp profile publication failed.")
            self.references[profile.identity] = reference
        return self.references[profile.identity]

    def lease(self):
        """New lifetime publication cannot race a final unload into a new authority."""
        with self.lane:
            if self.closing:
                raise LlmExecutionOwnershipError("Controlled llama.cpp final release is pending.")
            lease = _Lease(self)
            self.leases.add(lease)
            return lease

    def materialize(self, descriptor, token):
        """Load only an explicitly transitioned immutable startup profile."""
        profile = descriptor.providerPayload
        try:
            driver = _impl.LlamaCppDriver(profile.prepared, _authority=object())
        except Exception:
            return Materialization(_EMPTY_FAILURE, False)  # Constructor validates only; no child IO.
        if _impl._configuration.executableIdentity(driver.executable) != profile.executableObservation:
            return Materialization(_EMPTY_FAILURE, False)  # Known changed inputs require a new trusted profile.
        ticket = (token, driver, profile.model)
        self.resident = ticket
        try:
            with _impl._driverScope(driver):
                driver._startModel(profile.model)
            return Materialization(_OK, True, ticket)
        except Exception:
            if driver.process is None:
                self.resident = None
                return Materialization(_EMPTY_FAILURE, False)
            return Materialization(_UNKNOWN, True, ticket)

    def release(self, token, providerPayload):
        """Keep the exact process reference until stop/wait confirms owned exit."""
        if providerPayload is not self.resident or providerPayload[0] != token:
            return _UNKNOWN
        driver = providerPayload[1]
        try:
            with _impl._driverScope(driver):
                driver.stop()
        except Exception:
            return _UNKNOWN
        self.resident = None
        self.slots.clear()
        return _OK


class _ExecutionAdapter:
    """Full trusted ER-02 adapter, independent of direct facade methods."""

    supportsFresh = True
    supportsRequiredFresh = False
    supportsRetention = False

    def __init__(self, owner, facade, captureBroker=None, captureOwners=None):
        """Keep no Context or transaction; each admission owns one partial-open ticket."""
        self.owner, self.facade, self.tickets = owner, facade, {}
        self.captureBroker = captureBroker
        self.captureOwners = dict(captureOwners or {})

    def openExecution(self, admission, model, options):
        """Resolve only the admitted resident generation; never ensure/switch models."""
        session = _Session(self, admission, _impl._parseInferenceOptions(options))
        self.tickets[admission] = session
        session.requireResident()
        session.unregister = admission.bindStop(session.stop)
        return session

    def closeExecution(self, admission, session, reason):
        """Finalize exact-task evidence once; never repair a previous unknown result."""
        ticket = self.tickets.get(admission)
        try:
            return self._closeExecution(admission, session, reason)
        finally:
            if ticket is not None and ticket.capture is not None:
                ticket.capture.finish(mayHaveSent=ticket.sent,
                    headers=ticket.headersObserved, body=ticket.bodyObserved)

    def _closeExecution(self, admission, session, reason):
        """Compute actual cleanup before final evidence callbacks, outside sink code."""
        ticket = self.tickets.get(admission)
        if ticket is None:
            return LlmSessionCleanup(_OK)  # Parser failed before IO/partial allocation.
        with ticket.finalizeLane:
            if ticket.cleanupEffect is not None:
                return LlmSessionCleanup(ticket.cleanupEffect)
            effect = _UNKNOWN
            try:
                # An executing reader owns its transport. A concurrent failed
                # iterator close cannot let this finalizer steal that ownership.
                if ticket.readerDone.is_set() and not ticket.uncertain:
                    if not ticket.sent:
                        effect = _OK
                    elif reason is LlmSessionExit.COMPLETED and ticket.nativeDone and not ticket.stopped:
                        own = ticket.readSlot(timeout=min(ticket.options.timeoutSeconds, 1.0), cleanup=True)
                        if (own['is_processing'] is False and
                                (ticket.nativeTask is None or own.get('id_task') == ticket.nativeTask)):
                            effect = _OK
                    elif ticket.confirmStopped():
                        effect = _OK
            except Exception:
                effect = _UNKNOWN
            finally:
                if ticket.unregister is not None:
                    ticket.unregister()
            ticket.cleanupEffect = effect  # Terminal even if provider becomes idle later.
            if effect == _OK:
                with self.owner.lane:
                    self.owner.slots.discard(ticket.slot)
                self.tickets.pop(admission, None)
            return LlmSessionCleanup(effect)


class _Session:
    """One generation-bound estimator/context/transport session and cleanup ticket."""

    def __init__(self, adapter, admission, options):
        """Install private partial-open state before touching a concrete provider."""
        self.adapter, self.admission, self.options = adapter, admission, options
        self.resident = adapter.owner.resident
        self.driver = None if self.resident is None else self.resident[1]
        self.process = None if self.driver is None else self.driver.process
        self.model = None if self.resident is None else self.resident[2]
        self.response, self.unregister, self.slot = None, None, None
        self.sent = self.uncertain = self.nativeDone = self.stopped = False
        self.completionId, self.terminal = None, False
        self.transport = None
        self.stopLane, self.finalizeLane = RLock(), RLock()
        self.readerDone = Event()
        self.readerDone.set()
        self.cleanupEffect = None
        self.baselineTask = self.nativeTask = None
        self.correlationLost = False
        self.bindingAttempted = False
        self.capture = None
        self.headersObserved = self.bodyObserved = None

    def requireResident(self):
        """Reject stale/dying process objects before and after provider IO."""
        self.requireOwnedResidency()
        if not self.admission._claim.usable:
            raise LlmExecutionOwnershipError("Admitted llama.cpp execution is revoked.")

    def requireOwnedResidency(self):
        """Cleanup authority survives revocation, but not process/token/ticket replacement."""
        if (self.resident is None or self.adapter.owner.resident is not self.resident
                or self.admission not in self.adapter.tickets or self.cleanupEffect is not None
                or self.resident[0] != self.admission.residency
                or self.process is None or self.driver.process is not self.process or self.process.poll() is not None):
            raise LlmExecutionOwnershipError("Admitted llama.cpp residency is no longer available.")

    def stop(self):
        """Mark stop and interrupt only the owned raw socket, never the buffered reader."""
        with self.stopLane:
            self.stopped = True
            transport = self.transport
        if transport is not None:
            transport.stop()

    def readSlot(self, *, timeout, cleanup=False):
        """Read the exact owned slot in a complete validated native slot set."""
        require = self.requireOwnedResidency if cleanup else self.requireResident
        require()
        request = http.Request(f'{self.driver.baseUrl}/slots', method='GET')
        deadline = time.monotonic() + timeout
        transport = _transport.OwnedHttpTransport()
        pieces, size = [], 0
        try:
            response = transport.open(request, timeout=timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Managed slot acknowledgement arrived too late.')
                transport.setReadTimeout(remaining)
                piece = response.read1(min(65536, 1048577 - size))
                if not piece:
                    break
                pieces.append(piece)
                size += len(piece)
                if size > 1048576:
                    raise LlmProviderProtocolError('Managed slot acknowledgement exceeds its bound.')
        finally:
            transport.close()
        if time.monotonic() >= deadline:
            raise TimeoutError('Managed slot acknowledgement arrived too late.')
        raw = b''.join(pieces)
        slots = json.loads(raw.decode('utf-8'))
        require()
        if (type(slots) is not list or len(slots) != self.model.parallelSlots or
                not all(type(s) is dict and type(s.get('id')) is int and
                        type(s.get('is_processing')) is bool for s in slots) or
                {s['id'] for s in slots} != set(range(self.model.parallelSlots))):
            raise LlmProviderProtocolError('Managed slot acknowledgement has invalid ownership shape.')
        return next(s for s in slots if s['id'] == self.slot)

    def bindNativeTask(self):
        """Bind once while this sole admitted request actively owns its native slot.

        Managed slot allocation excludes another queued request for this slot.
        The baseline must be idle; monotonic task IDs must advance beyond any
        previous task. SSE IDs remain separate and cannot substitute for id_task.
        """
        try:
            own = self.readSlot(timeout=min(self.options.timeoutSeconds, 1.0))
            task = own.get('id_task')
            if own['is_processing'] is not True:
                return  # Already finished: normal terminal path may still establish drain.
            if (type(task) is not int or task < 0 or
                    (self.baselineTask is not None and task <= self.baselineTask)):
                self.correlationLost = True
                return
            self.nativeTask = task
        except Exception:
            self.correlationLost = True

    def confirmStopped(self):
        """Bound exact same-task idle proof before finalization, otherwise keep unknown.

        A two-second maximum observation budget (also limited by request timeout)
        is not a stop guarantee. Revoked admission retains cleanup authority only.
        Missing/mismatched IDs, changed residency/process and late replies fail shut.
        """
        if self.nativeTask is None or self.completionId is None or self.correlationLost:
            return False
        deadline = time.monotonic() + min(self.options.timeoutSeconds, 2.0)
        idle = 0
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            own = self.readSlot(timeout=min(remaining, 0.5), cleanup=True)
            if time.monotonic() >= deadline or type(own.get('id_task')) is not int or own['id_task'] != self.nativeTask:
                return False
            idle = idle + 1 if own['is_processing'] is False else 0
            if idle == 2:
                return True
            Event().wait(min(0.05, max(0, deadline - time.monotonic())))
        return False

    def getExecutionProfile(self):
        """Expose existing safe LC metadata and an estimator scoped to this ticket."""
        self.requireResident()
        return LlmExecutionProfile(contextWindowTokens=self.model.contextWindowTokens,
            tokenEstimator=self, metadata={"timeoutSeconds": self.options.timeoutSeconds,
                "managedServer": True, "configurationEvidence": self.driver.configurationSummary(self.model.name),
                "tokenEstimation": _impl._estimationMetadata(), "launchProfile": self.model.launchProfile()})

    def messages(self, query):
        """Preserve structured messages on the manifest path and plain substrate queries."""
        if query.formatId == "text/plain" and type(query.payload) is str:
            return [{"role": "user", "content": query.payload}]
        if query.formatId == LLM_MESSAGES_FORMAT_ID and self.adapter.facade.structured:
            return LlmMessages.fromPayload(query.payload).snapshot()
        raise LlmProviderProtocolError("Unsupported llama.cpp query format.")

    def estimateInputTokens(self, query):
        """Render/tokenize within the same exact generation and immutable controls."""
        self.requireResident()
        if self.admission not in self.adapter.tickets:
            raise LlmExecutionOwnershipError("Estimator session has closed.")
        controls = _impl._templateControls(self.options)
        self.uncertain = True
        with _impl._driverScope(self.driver):
            rendered = self.driver.postJson("/apply-template", {"messages": self.messages(query), **controls},
                timeoutSeconds=self.options.timeoutSeconds)
            self.requireResident()
            if type(rendered.get("prompt")) is not str:
                raise LlmProviderProtocolError("Invalid llama.cpp rendered prompt.")
            tokenized = self.driver.postJson("/tokenize", _impl._tokenizePayload(rendered["prompt"], controls),
                timeoutSeconds=self.options.timeoutSeconds)
        self.requireResident()
        self.uncertain = False
        return _impl._tokenCount(tokenized)

    def prepareContext(self, query, request):
        """Allocate only an owned idle slot; disable reuse as a best-effort request."""
        self.requireResident()
        self.messages(query)
        with self.adapter.owner.lane:
            available = set(range(self.model.parallelSlots)) - self.adapter.owner.slots
            if not available:
                raise LlmProviderConnectionError("Managed llama.cpp slot capacity is busy.")
            self.slot = min(available)
            self.adapter.owner.slots.add(self.slot)
        return LlmContextEffect(EffectStatus.ATTEMPTED)

    def trackedLines(self):
        """Validate native terminal/ID independently of the legacy completed event."""
        try:
            yield from self._trackedLines()
        except LlmProviderProtocolError:
            self.correlationLost = True
            raise

    def _trackedLines(self):
        """Parse private native identity and attempt active binding before yielding data."""
        for raw in self.response:
            self.bodyObserved = True
            if type(raw) is bytes and raw.startswith(b"data:"):
                data = raw[5:].strip()
                if data == b"[DONE]":
                    if not self.terminal or self.completionId is None:
                        raise LlmProviderProtocolError("Managed llama.cpp lacks a native terminal completion.")
                    self.nativeDone = True
                else:
                    try:
                        chunk = json.loads(data)
                    except (ValueError, UnicodeError):
                        raise LlmProviderProtocolError("Invalid managed llama.cpp stream JSON.") from None
                    if type(chunk) is not dict:
                        raise LlmProviderProtocolError("Invalid managed llama.cpp stream object.")
                    ident = chunk.get("id")
                    if type(ident) is not str or not ident or (self.completionId is not None and ident != self.completionId):
                        raise LlmProviderProtocolError("Managed llama.cpp completion identity is missing or inconsistent.")
                    self.completionId = ident
                    choices = chunk.get("choices", [])
                    if type(choices) is not list or (choices and (len(choices) != 1 or type(choices[0]) is not dict)):
                        raise LlmProviderProtocolError("Invalid managed llama.cpp stream choices.")
                    if choices:
                        if self.terminal:
                            raise LlmProviderProtocolError("Managed llama.cpp emitted content after terminal completion.")
                        finish = choices[0].get("finish_reason")
                        if finish is not None:
                            if finish not in ("stop", "length", "tool_calls", "content_filter", "function_call"):
                                raise LlmProviderProtocolError("Invalid native llama.cpp finish reason.")
                            self.terminal = True
                    if not self.bindingAttempted:
                        self.bindingAttempted = True
                        if not self.terminal and not self.stopped:
                            self.bindNativeTask()
            yield raw

    def stream(self, request):
        """Enter only this admitted request/slot; cancellation does not confirm drain."""
        self.requireResident()
        if self.stopped:
            raise LlmExecutionOwnershipError("Managed llama.cpp execution has stopped.")
        signal = request.cancellationSignal
        if signal is not None:
            signal.raiseIfRequested()
        policy = self.adapter.captureOwners.get(self.admission.callerIdentity)
        if policy is not None and policy[0]:
            self.capture = self.adapter.captureBroker.claim(self.admission.callerIdentity, self.admission)
        payload = _impl._buildPayload(request, self.options, includeRequestedModel=False) if not self.adapter.facade.structured else {
            "messages": self.messages(request.query), "stream": True, "temperature": self.options.temperature,
            **{key: value for key, value in (("max_tokens", self.options.maxTokens), ("top_p", self.options.topP),
                ("top_k", self.options.topK), ("min_p", self.options.minP), ("repeat_penalty", self.options.repeatPenalty),
                ("seed", self.options.seed)) if value is not None}, **_impl._templateControls(self.options)}
        payload.update(id_slot=self.slot, cache_prompt=False)
        transport = http.Request(f"{self.driver.baseUrl}/v1/chat/completions", data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"}, method="POST")
        unregister = None
        try:
            if self.capture is not None:
                self.capture.serialized(transport.data, policy[1])
                self.requireResident()
                if signal is not None:
                    signal.raiseIfRequested()
            baseline = self.readSlot(timeout=min(self.options.timeoutSeconds, 1.0))
            if baseline['is_processing'] is not False:
                self.uncertain = True
                raise LlmProviderProtocolError('The admitted native slot was already active before send.')
            previous = baseline.get('id_task')
            if type(previous) is int and previous >= 0:
                self.baselineTask = previous
            elif 'id_task' in baseline:
                self.correlationLost = True
            self.requireResident()
            self.readerDone.clear()
            with self.stopLane:
                self.transport = _transport.OwnedHttpTransport()
                if self.stopped:
                    self.transport.stop()
            if signal is not None:
                unregister = signal.registerCallback(self.stop)
                signal.raiseIfRequested()
            if self.capture is not None:
                self.capture.attempt()
                self.requireResident()
                if signal is not None:
                    signal.raiseIfRequested()
                if self.stopped:
                    raise LlmExecutionOwnershipError("Managed llama.cpp execution has stopped.")
            self.response = self.transport.open(transport, timeout=self.options.timeoutSeconds)
            self.headersObserved = True
            if self.capture is not None:
                self.capture.requireLive()
                self.capture.emit("response-headers-observed", responseHeadersObserved=True)
                self.requireResident()
                if signal is not None:
                    signal.raiseIfRequested()
            yield from _impl._readEvents(self.trackedLines())
        except (http.HTTPError, http.URLError, OSError):
            raise LlmProviderConnectionError("Failed communicating with managed llama.cpp.") from None
        finally:
            if unregister is not None:
                unregister()
            try:
                if self.transport is not None:
                    self.sent = self.transport.mayHaveSent
                    self.transport.close()  # The reader alone owns buffered close.
            except Exception:
                self.uncertain = True
                # Keep the original cancellation/provider exception primary.
                # A nominal completion instead fails at cleanup finalization.
            finally:
                self.readerDone.set()
