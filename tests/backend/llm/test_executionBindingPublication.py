# file: tests/backend/llm/test_executionBindingPublication.py ; version: 1
"""Binding publication must linearize with revocation and dispose losing ownership."""
from threading import Event
from contextlib import contextmanager

import pytest

from backend.llm.errors import LlmExecutionOwnershipError
from backend.llm.executionSessions import LlmOwnerGrant
from backend.llm.streamingRuntime import LlmProviderRegistry, LlmProcessingPipeline
from backend.registration import RegistrationScope
from backend.runtime.executionResources import ExecutionResourceDomain, ResidencyDescriptor, ResourceState
from tests.backend.llm.test_executionSessions import Adapter, Legacy, Residency, background, QUERY


class Publication:
    """Tracks actual issued/closed participants and separately composition-owned lease."""

    def __init__(self, monkeypatch, *, ownerCount=1):
        """Installs a published registration and host grants without a binding."""
        self.registry = LlmProviderRegistry()
        self.scope = RegistrationScope()
        self.provider = Legacy()
        self.registry.register(self.scope, ownerId="provider", name="controlled", provider=self.provider)
        self.scope.publish()
        self.registration = self.registry.requireRegistration("controlled")
        self.service = self.registry.executions
        self.owners = tuple(self.service.owner(self.registry) for _ in range(ownerCount))
        self.residency = Residency()
        self.domain = ExecutionResourceDomain("publication", self.residency)
        self.profile = self.domain.registerProfile(ResidencyDescriptor(b"profile", object()))
        self.adapter = Adapter(self.residency)
        self.issued, self.closed, self.retired, self.leases = [], [], [], []
        self.entered, self.proceed = Event(), Event()
        self.pause = False
        self.failAt = None
        self.closeProbe = None
        self.attempts = 0
        self.original = self.domain.openParticipant
        monkeypatch.setattr(self.domain, "openParticipant", self.issue)

    def issue(self, **kwargs):
        """Pauses after actual issuance or fails a later construction step."""
        self.attempts += 1
        if self.attempts == self.failAt:
            raise ValueError("partial construction failure")
        participant = self.original(**kwargs)
        self.issued.append(participant)
        originalClose = participant.close

        def close():
            """Counts actual rollback/disposition and probes absence of enclosing locks."""
            self.closed.append(participant)
            if self.closeProbe is not None:
                self.closeProbe()
            result = originalClose()
            if not result.duplicate:
                self.retired.append(participant)
            return result

        participant.close = close
        if self.pause and len(self.issued) == 1:
            self.entered.set()
            assert self.proceed.wait(5)
        return participant

    def leaseClose(self):
        """Represents the caller's lease until successful binding publication."""
        self.leases.append(1)

    def bind(self, *, owners=None, registration=None):
        """Attempts trusted publication with explicit composition-owned lease."""
        return self.service.bind(self.registry, registration or self.registration,
            domain=self.domain, profiles={None: self.profile}, adapter=self.adapter,
            grants={owner: LlmOwnerGrant((None,), True) for owner in (owners or self.owners)},
            leaseCloser=self.leaseClose)

    def revoke(self, kind):
        """Performs a real lifecycle event, including withdrawal without broker notification."""
        if kind == "host":
            self.service.revokeAll()
        elif kind == "registry":
            self.service.revokeRegistry(self.registry)
        elif kind == "temporary-registry":
            self.service.revokeRegistry(self.registry, permanent=False)
        elif kind == "owner":
            self.service.revokeOwner(self.owners[0])
        elif kind == "registration":
            self.service.revokeRegistration(self.registry, "provider")
            self.scope.withdraw()
        elif kind == "provider-only":
            self.service.revokeRegistration(self.registry, "provider")
        elif kind == "withdraw":
            self.scope.withdraw()
        else:
            raise AssertionError(kind)

    def assertRejectedDisposition(self):
        """Asserts real closure and explicit caller lease responsibility, not only an error."""
        assert not self.service._bindings
        assert not self.service._pendingPublications
        assert len(self.issued) == len(self.closed)
        assert all(self.closed.count(participant) == 1 for participant in self.issued)
        assert all(participant._closed for participant in self.issued)
        assert len(self.retired) == len(self.issued)
        assert self.leases == []  # Rejection never transfers the composition lease.
        self.service.finishClose()
        assert self.leases == []
        self.leaseClose()  # Explicit caller-owned disposition after rejected bind.
        self.service.finishClose()
        assert self.leases == [1]
        assert self.domain.snapshot.state is ResourceState.EMPTY
        assert self.adapter.opens == self.adapter.closes == self.residency.loads == []


@pytest.mark.parametrize("kind", ("host", "registry", "owner", "registration"))
def test_binding_cannot_publish_after_winning_revocation(monkeypatch, kind):
    """The four independent Integration failures become lasting ownership regressions."""
    case = Publication(monkeypatch)
    case.pause = True
    future = background(case.bind)
    try:
        assert case.entered.wait(5)
        case.revoke(kind)
    finally:
        case.proceed.set()
    with pytest.raises(LlmExecutionOwnershipError):
        future.result(5)
    case.assertRejectedDisposition()


@pytest.mark.parametrize("kind", ("temporary-registry", "provider-only", "withdraw"))
def test_unload_or_unnotified_withdrawal_also_prevents_late_publication(monkeypatch, kind):
    """Exact withdrawal and temporary revocation cannot miss an unpublished binding."""
    case = Publication(monkeypatch)
    case.pause = True
    future = background(case.bind)
    try:
        assert case.entered.wait(5)
        case.revoke(kind)
    finally:
        case.proceed.set()
    with pytest.raises(LlmExecutionOwnershipError):
        future.result(5)
    case.assertRejectedDisposition()


@pytest.mark.parametrize("kind", ("host", "registry", "owner", "provider-only"))
def test_revocation_before_any_binding_request_is_remembered(monkeypatch, kind):
    """A revocation event does not require an existing published or pending binding."""
    case = Publication(monkeypatch)
    case.revoke(kind)
    with pytest.raises(LlmExecutionOwnershipError):
        case.bind()
    assert case.issued == []
    case.assertRejectedDisposition()


@pytest.mark.parametrize("kind", ("host", "registry", "owner", "provider-only"))
def test_publication_winning_before_revocation_has_normal_lease_disposition(monkeypatch, kind):
    """Successful publication transfers the lease and normal cleanup releases it once."""
    case = Publication(monkeypatch)
    binding = case.bind()
    case.revoke(kind)
    case.service.finishClose()
    case.service.finishClose()
    if kind == "owner":
        assert case.leases == []  # Caller owner close alone does not revoke provider lifetime.
        case.service.revokeRegistration(case.registry, "provider")
        case.service.finishClose()
        case.service.finishClose()
    assert binding.revoked
    assert case.leases == [1]
    assert all(p._closed for p in case.issued)
    assert len(case.retired) == len(case.issued)
    assert case.domain.snapshot.state is ResourceState.EMPTY


def test_partial_construction_rolls_back_all_prior_issued_participants(monkeypatch):
    """A later constructor failure cannot strand participants issued for earlier grants."""
    case = Publication(monkeypatch, ownerCount=3)
    case.failAt = 3
    with pytest.raises(ValueError, match="partial construction failure"):
        case.bind()
    assert len(case.issued) == 2
    case.assertRejectedDisposition()


def test_rollback_is_outside_broker_registry_and_domain_lanes(monkeypatch):
    """Second-thread entry probes prove rollback does not rely on RLock reentry."""
    case = Publication(monkeypatch)
    case.pause = True
    probes = []

    def probe():
        """Requires all three actual lanes from a separate thread before close returns."""
        def read():
            """Touches registry, broker and domain through real read paths."""
            case.registry._registry.snapshot()
            with case.service._lane:
                assert case.service._pendingPublications
            return case.domain.snapshot
        probes.append(background(read).result(5))

    case.closeProbe = probe
    future = background(case.bind)
    try:
        assert case.entered.wait(5)
        case.revoke("owner")
    finally:
        case.proceed.set()
    with pytest.raises(LlmExecutionOwnershipError):
        future.result(5)
    assert len(probes) == 1
    case.assertRejectedDisposition()


def test_new_owner_incarnation_can_bind_after_rejected_old_owner_publication(monkeypatch):
    """An old owner revocation does not grant it authority or revoke a newly issued owner."""
    case = Publication(monkeypatch)
    case.pause = True
    future = background(case.bind)
    try:
        assert case.entered.wait(5)
        case.revoke("owner")
        replacement = case.service.owner(case.registry)
    finally:
        case.proceed.set()
    with pytest.raises(LlmExecutionOwnershipError):
        future.result(5)
    case.pause = False
    binding = case.bind(owners=(replacement,))
    assert set(binding.participants) == {replacement}
    case.service.revokeRegistration(case.registry, "provider")
    case.service.finishClose()
    assert case.leases == [1]


def test_registration_reincarnation_requires_new_exact_binding_and_old_stays_revoked(monkeypatch):
    """Provider/name/owner text reuse cannot make a rejected registration authoritative."""
    case = Publication(monkeypatch)
    case.service.revokeRegistration(case.registry, "provider")
    case.scope.withdraw()
    replacementScope = RegistrationScope()
    case.registry.register(replacementScope, ownerId="provider", name="controlled", provider=case.provider)
    replacementScope.publish()
    replacement = case.registry.requireRegistration("controlled")
    with pytest.raises(LlmExecutionOwnershipError):
        case.bind(registration=case.registration)
    binding = case.bind(registration=replacement)
    assert binding.registration is replacement and not binding.revoked
    case.service.revokeRegistration(case.registry, "provider")
    case.service.finishClose()
    assert case.leases == [1]


def test_pending_or_failed_controlled_publication_never_uses_legacy_fallback(monkeypatch):
    """An unpublished controlled intent does not silently become unmanaged execution."""
    case = Publication(monkeypatch, ownerCount=2)
    case.pause, case.failAt = True, 2
    pipeline = LlmProcessingPipeline(providers=case.registry)
    access = case.owners[0].invocation()
    future = background(case.bind)
    try:
        assert case.entered.wait(5)
        with pytest.raises(LlmExecutionOwnershipError):
            pipeline.run(providerName="controlled", query=QUERY, executionAccess=access)
    finally:
        case.proceed.set()
    with pytest.raises(ValueError, match="partial construction failure"):
        future.result(5)
    with pytest.raises(LlmExecutionOwnershipError):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access)
    assert case.provider.calls == []
    case.assertRejectedDisposition()


def test_pending_publication_has_an_explicit_nontransferred_close_disposition(monkeypatch):
    """Close reports a constructing ticket as pending instead of claiming lifetime drain."""
    case = Publication(monkeypatch)
    case.pause = True
    future = background(case.bind)
    try:
        assert case.entered.wait(5)
        case.revoke("host")
        with pytest.raises(ExceptionGroup):
            case.service.finishClose()
        assert case.leases == [] and case.closed == []
    finally:
        case.proceed.set()
    with pytest.raises(LlmExecutionOwnershipError):
        future.result(5)
    case.assertRejectedDisposition()


def test_duplicate_request_does_not_construct_a_second_pending_binding(monkeypatch):
    """An exact publication ticket has one constructor and never steals the losing lease."""
    case = Publication(monkeypatch)
    case.pause = True
    future = background(case.bind)
    try:
        assert case.entered.wait(5)
        with pytest.raises(LlmExecutionOwnershipError):
            case.bind()
        assert len(case.issued) == 1 and case.closed == [] and case.leases == []
    finally:
        case.proceed.set()
    binding = future.result(5)
    assert case.service.binding(case.registry, case.registration) is binding
    case.service.revokeRegistration(case.registry, "provider")
    case.service.finishClose()
    assert case.leases == [1] and case.retired == case.issued


def test_known_partial_construction_failure_allows_explicit_caller_owned_retry(monkeypatch):
    """Retry is explicit after complete rollback; the original lease has not been consumed."""
    case = Publication(monkeypatch, ownerCount=2)
    case.failAt = 2
    with pytest.raises(ValueError):
        case.bind()
    assert len(case.retired) == 1 and case.leases == []
    assert not case.service._pendingPublications
    case.failAt = None
    binding = case.bind()
    assert len(binding.participants) == 2
    case.service.revokeRegistration(case.registry, "provider")
    case.service.finishClose()
    case.service.finishClose()
    assert case.leases == [1]
    assert len(case.retired) == len(case.issued) == 3


def test_final_registration_commit_and_scope_withdrawal_share_the_same_lane(monkeypatch):
    """Withdrawal cannot fit between the exact-registration check and binding commit."""
    case = Publication(monkeypatch)
    original = case.registry.executionPublication
    entered, proceed, withdrawing = Event(), Event(), Event()
    visits = []

    @contextmanager
    def guard():
        """Pauses with the real registry lane held at final publication."""
        with original() as registrations:
            visits.append(1)
            if len(visits) == 2:
                entered.set()
                assert proceed.wait(5)
            yield registrations

    def withdraw():
        """Attempts actual scope withdrawal while final publication owns the lane."""
        withdrawing.set()
        case.scope.withdraw()

    monkeypatch.setattr(case.registry, "executionPublication", guard)
    publication = background(case.bind)
    withdrawal = None
    try:
        assert entered.wait(5)
        withdrawal = background(withdraw)
        assert withdrawing.wait(5)
        assert not withdrawal.done()
    finally:
        proceed.set()
    binding = publication.result(5)
    withdrawal.result(5)
    assert case.service._bindings[(id(case.registry), id(case.registration))] is binding
    case.service.revokeRegistration(case.registry, "provider")
    case.service.finishClose()
    assert case.leases == [1] and case.retired == case.issued


def test_failure_after_commit_does_not_roll_back_transferred_lifetime(monkeypatch):
    """A post-commit interruption cannot treat the now-published binding as unpublished."""
    case = Publication(monkeypatch)
    original = case.registry.executionPublication
    visits = []

    @contextmanager
    def guard():
        """Injects a guard-exit failure only after successful atomic publication."""
        with original() as registrations:
            visits.append(1)
            yield registrations
        if len(visits) == 2:
            raise RuntimeError("post-commit interruption")

    monkeypatch.setattr(case.registry, "executionPublication", guard)
    with pytest.raises(RuntimeError, match="post-commit interruption") as failure:
        case.bind()
    assert failure.value.llmBindingPublished
    assert case.closed == [] and case.leases == []
    binding = case.service.binding(case.registry, case.registration)
    assert not binding.revoked and not case.service._pendingPublications
    monkeypatch.setattr(case.registry, "executionPublication", original)
    case.service.revokeRegistration(case.registry, "provider")
    case.service.finishClose()
    assert case.leases == [1] and case.retired == case.issued


def test_preconstruction_revoke_rejection_cannot_fall_back_to_legacy(monkeypatch):
    """An explicit controlled request remains fail-closed even if revocation preceded it."""
    case = Publication(monkeypatch)
    case.service.revokeRegistration(case.registry, "provider")
    with pytest.raises(LlmExecutionOwnershipError):
        case.bind()
    pipeline = LlmProcessingPipeline(providers=case.registry)
    with pytest.raises(LlmExecutionOwnershipError):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=case.owners[0].invocation())
    assert case.provider.calls == []
    case.assertRejectedDisposition()


def test_rollback_attempts_every_participant_and_reports_incomplete_cleanup(monkeypatch):
    """A failed close cannot mask construction failure or orphan an unreported disposition."""
    case = Publication(monkeypatch, ownerCount=3)
    case.failAt = 3
    probes = []

    def failFirstClose():
        """Fails one rollback hook while allowing remaining issued participants to close."""
        probes.append(1)
        if len(probes) == 1:
            raise OSError("private close failure")

    case.closeProbe = failFirstClose
    with pytest.raises(ValueError, match="partial construction failure") as failure:
        case.bind()
    assert failure.value.llmPublicationCleanup is not None
    assert len(case.closed) == 2 and len(case.retired) == 1
    assert not case.service._pendingPublications and not case.service._bindings
    assert case.leases == []
    for _ in range(2):
        with pytest.raises(ExceptionGroup):
            case.service.finishClose()
    assert len(case.closed) == 2  # No hidden recovery or duplicate close attempts.
    case.leaseClose()
    assert case.leases == [1]


def test_host_revokes_a_published_binding_without_any_owner_grants(monkeypatch):
    """Host lifetime includes binding leases even before any CodeEntry grants exist."""
    case = Publication(monkeypatch, ownerCount=0)
    binding = case.bind()
    assert case.issued == [] and binding.participants == {}
    case.service.revokeAll()
    case.service.finishClose()
    case.service.finishClose()
    assert binding.revoked and case.leases == [1]
