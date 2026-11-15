# file: tests/first_party/llmDrivers/controlledLlama.py ; version: 1
"""Trusted fixtures: substrate mechanisms and real ER-02 binding publication.

launchOwnedDriver/profileOwnedDriver test only LC mechanism/evidence in an explicit
adapter scope. They make no admission claim; end-to-end admission is tested through
ManagedBinding and the real manifest/host fixture in test_llamaCppExecutionResources.
"""
from pathlib import Path

from backend.application.applicationRuntime import ApplicationRuntime
from backend.context.codeEntryContext import CodeEntryIdentity
from backend.llm.executionSessions import executionBoundary, LlmExecutionBindings
from backend.llm.providerComposition import ActivationCompositionSnapshot
from backend.packs.runtime import PackResolver
from backend.registration import RegistrationScope
from backend.runtime.executionResources import ExecutionResourceService
from tests.first_party.llmDrivers.test_llamaCppExecutionResources import adapter


def launchOwnedDriver(driver):
    """Exercise authorized low-level startup without calling public ensureModel."""
    if driver._authority is None:
        driver._authority = object()  # This fixture is the trusted adapter issuer.
    with adapter._impl._driverScope(driver):
        if driver.process is None or driver.process.poll() is not None:
            driver._startModel(driver._selectedModel(None))


def stopOwnedDriver(driver):
    """Exercise trusted owned-process disposal, never a public consumer bypass."""
    with adapter._impl._driverScope(driver):
        driver.stop()


def profileOwnedDriver(provider, **options):
    """Inspect both LC metadata projections inside the low-level adapter scope."""
    launchOwnedDriver(provider.driver)
    with adapter._impl._driverScope(provider.driver):
        return provider.getExecutionProfile(**options)


class SharedComposition:
    """Real service/broker shared by independently issued Application owners."""

    def __init__(self):
        """Build inert services; no subprocess or provider is executed."""
        self.resources, self.bindings = ExecutionResourceService(), LlmExecutionBindings()

    def bind(self, config):
        """Construct one actual runtime/registration and trusted immutable grant."""
        return ManagedBinding(self, config)


class ManagedBinding:
    """Test host composition with real runtime-issued owners and exact registration."""

    def __init__(self, shared, config):
        """Snapshot launch inputs and bind a controlled managed facade lazily."""
        catalog = adapter.LlamaCppCatalog.fromConfig(config)
        self.runtime = runtime = ApplicationRuntime(appPackId="fixture", packResolver=PackResolver(roots=()),
            llmExecutionBindings=shared.bindings, executionResourceService=shared.resources)
        runtime.start()
        identities = []
        for name in ("provider", "caller"):
            identity = CodeEntryIdentity(applicationId=runtime.applicationRun.application.applicationId,
                applicationRunId=runtime.applicationRun.applicationRunId, packId="fixture", packVersion="1",
                codeEntryId=name, codeEntryInstanceId=name, sourceSha256=name,
                implementationFormat="python-source@1", implementationId=name)
            runtime.registerCodeEntry(identity, Path.cwd())
            identities.append(identity)
        self.scope = RegistrationScope()
        runtime.llmProviders.register(self.scope, ownerId="provider", name="llama.cpp",
            provider=adapter._impl._ManagedFacade(config, structured=True))
        self.scope.publish()
        provider, caller = (adapter.EntrySelector(i.packId, i.packVersion, i.codeEntryId, i.sourceSha256) for i in identities)
        self.composer = adapter.LlamaCppComposition(domainId="fixture", catalog=catalog,
            providerEntries=(provider,), grants=(adapter.LlamaCppGrant(caller, tuple(catalog.profiles), True),))
        self.composer.compose(ActivationCompositionSnapshot(shared.resources, shared.bindings,
            runtime.llmProviders, (runtime.llmProviders.requireRegistration("llama.cpp"),),
            tuple((i, runtime._llmOwners[i.codeEntryInstanceId]) for i in identities), self))
        self.owner = runtime._llmOwners["caller"]
        self.binding = shared.bindings.binding(runtime.llmProviders, runtime.llmProviders.requireRegistration("llama.cpp"))

    def requireCurrent(self):
        """Manual trusted composition still checks actual runtime lifetime."""
        if self.runtime._closed:
            raise RuntimeError("Fixture runtime closed.")

    @property
    def driver(self):
        """Return the exact real adapter-owned driver after explicit materialization."""
        return self.binding.adapter.owner.resident[1]

    def transition(self, model=None):
        """Use accepted issued-owner profile/transition APIs, without auto-load."""
        access = self.owner.invocation()
        try:
            profile = self.runtime.llmExecutions.profile(self.runtime.llmProviders, access, "llama.cpp", model)
            return self.runtime.llmExecutions.transition(access, profile)
        finally:
            access.close()

    def run(self, **options):
        """Run through the real controlled session pipeline with fresh access."""
        access = self.owner.invocation()
        try:
            return self.runtime.llmPipeline.run(providerName="llama.cpp", executionAccess=access, **options)
        finally:
            access.close()

    def close(self):
        """Withdraw exact binding/owner lifetimes and allow final drained release."""
        self.runtime.close()
        self.scope.withdraw()
