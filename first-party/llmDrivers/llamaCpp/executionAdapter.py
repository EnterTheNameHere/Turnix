# file: first-party/llmDrivers/llamaCpp/executionAdapter.py ; version: 1
"""Trusted llama.cpp adoption of ER-01 residency and ER-02 invocation sessions.

Stock HTTP cannot require physical cache reuse. Retention and required freshness
reject; ordinary freshness requests disable prompt reuse without hidden-state claims.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
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

    def __init__(self, *, domainId, catalog, providerEntries, grants):
        """Record explicit host authority independently of mutable Application config."""
        if not isinstance(catalog, LlamaCppCatalog):
            raise TypeError("Expected a trusted LlamaCppCatalog.")
        if type(domainId) is not str or not domainId:
            raise ValueError("Controlled domain ID must be nonempty.")
        self.domainId, self.catalog = domainId, catalog
        self.providerEntries, self.grants = tuple(providerEntries), tuple(grants)

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
        existing = snapshot.bindings.binding(snapshot.registry, registration)
        facade = registration.value
        if type(facade) is not _impl._ManagedFacade or facade.requestedConfiguration != self.catalog.values:
            raise LlmExecutionOwnershipError("Managed llama.cpp configuration or facade is not host-approved.")
        if existing is not None:
            with _tableLane:
                owner = _domains.get(snapshot.resources, {}).get(self.domainId)
                if owner is None or owner.envelope != self.catalog.envelope or existing.domain is not owner.domain:
                    raise LlmExecutionOwnershipError("Controlled llama.cpp domain envelope conflicts.")
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
                profiles=profiles, adapter=_ExecutionAdapter(owner, facade), grants=grants, leaseCloser=lease.close)
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

    def __init__(self, owner, facade):
        """Keep no Context or transaction; each admission owns one partial-open ticket."""
        self.owner, self.facade, self.tickets = owner, facade, {}

    def openExecution(self, admission, model, options):
        """Resolve only the admitted resident generation; never ensure/switch models."""
        session = _Session(self, admission, _impl._parseInferenceOptions(options))
        self.tickets[admission] = session
        session.requireResident()
        session.unregister = admission.bindStop(session.stop)
        return session

    def closeExecution(self, admission, session, reason):
        """Separate local close from source-backed completion or unknown disposition."""
        ticket = self.tickets.get(admission)
        if ticket is None:
            return LlmSessionCleanup(_OK)  # Parser failed before IO/partial allocation.
        effect = _UNKNOWN
        try:
            if ticket.response is not None:
                ticket.response.close()
            if not ticket.uncertain:
                if not ticket.sent:
                    effect = _OK
                elif reason is LlmSessionExit.COMPLETED and ticket.nativeDone and not ticket.stopped:
                    ticket.requireResident()
                    request = http.Request(f"{ticket.driver.baseUrl}/slots", method="GET")
                    with http.urlopen(request, timeout=ticket.options.timeoutSeconds) as response:
                        slots = json.loads(response.read().decode("utf-8"))
                    ticket.requireResident()
                    if (type(slots) is list and len(slots) == ticket.model.parallelSlots
                            and all(type(s) is dict and type(s.get("id")) is int for s in slots)
                            and {s["id"] for s in slots} == set(range(ticket.model.parallelSlots))):
                        own = next(s for s in slots if s["id"] == ticket.slot)
                        if own.get("is_processing") is False:
                            effect = _OK
        except Exception:
            effect = _UNKNOWN
        finally:
            if ticket.unregister is not None:
                ticket.unregister()
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
        self.model = None if self.resident is None else self.resident[2]
        self.response, self.unregister, self.slot = None, None, None
        self.sent = self.uncertain = self.nativeDone = self.stopped = False
        self.completionId, self.terminal = None, False

    def requireResident(self):
        """Reject stale/dying process objects before and after provider IO."""
        if (self.resident is None or self.adapter.owner.resident is not self.resident
                or self.admission not in self.adapter.tickets or not self.admission._claim.usable
                or self.resident[0] != self.admission.residency
                or self.driver.process is None or self.driver.process.poll() is not None):
            raise LlmExecutionOwnershipError("Admitted llama.cpp residency is no longer available.")

    def stop(self):
        """Cooperatively close local transport; this is never quiescence evidence."""
        self.stopped = True
        if self.response is not None:
            self.response.close()

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
        for raw in self.response:
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
            yield raw

    def stream(self, request):
        """Enter only this admitted request/slot; cancellation does not confirm drain."""
        self.requireResident()
        if self.stopped:
            raise LlmExecutionOwnershipError("Managed llama.cpp execution has stopped.")
        signal = request.cancellationSignal
        if signal is not None:
            signal.raiseIfRequested()
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
            self.sent = True  # Failure may occur after the server accepted the request.
            self.response = http.urlopen(transport, timeout=self.options.timeoutSeconds)
            if self.stopped:
                self.response.close()
                raise LlmExecutionOwnershipError("Managed llama.cpp execution has stopped.")
            if signal is not None:
                unregister = signal.registerCallback(self.stop)
                signal.raiseIfRequested()
            with self.response:
                yield from _impl._readEvents(self.trackedLines())
        except (http.HTTPError, http.URLError, TimeoutError):
            raise LlmProviderConnectionError("Failed communicating with managed llama.cpp.") from None
        finally:
            if unregister is not None:
                unregister()
