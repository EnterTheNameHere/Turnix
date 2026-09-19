# file: tests/first_party/llmDrivers/test_llamaCppSharedRuntime.py ; version: 3
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from backend.runtime.sharedServices import (
    SharedServiceRegistry,
    bindApplicationRunSharedServices,
    unbindApplicationRunSharedServices,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch

_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "llmDrivers"
    / "llamaCpp"
    / "structuredCodeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("llamaCppStructuredSharedTest", _CODE_ENTRY)
assert _SPEC is not None
assert _SPEC.loader is not None
llamaCpp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(llamaCpp)


class _FakeDriver:
    """Records construction/start/stop while avoiding a real llama.cpp process."""

    instances: ClassVar[list[_FakeDriver]] = []

    def __init__(self, config: dict[str, object]) -> None:
        """Captures one effective driver configuration."""
        self.config = dict(config)
        self.startCalls = 0
        self.stopCalls = 0
        _FakeDriver.instances.append(self)

    def start(self) -> None:
        """Records eager startup if the adapter incorrectly performs it."""
        self.startCalls += 1

    def stop(self) -> None:
        """Records disposal of the shared managed resource."""
        self.stopCalls += 1


class _FakeLlmFacade:
    """Captures the provider registered by one application CodeEntry."""

    def __init__(self) -> None:
        """Creates an empty provider capture."""
        self.providers: dict[str, object] = {}

    def registerProvider(self, name: str, provider: object) -> None:
        """Records one provider publication."""
        self.providers[name] = provider


def _context(applicationRunId: str) -> SimpleNamespace:
    """Builds the minimum CodeEntry context surface required by llama.cpp onLoad."""
    return SimpleNamespace(
        config={
            "llamaCpp": {
                "manageServer": True,
                "port": 8080,
                "models": {"model-a": {"modelPath": "model.gguf"}},
                "defaultModel": "model-a",
            },
        },
        identity=SimpleNamespace(applicationRunId=applicationRunId),
        llm=_FakeLlmFacade(),
    )


def test_managed_driver_is_shared_and_lazy_across_application_runs(monkeypatch: MonkeyPatch) -> None:
    """Two application-local providers lease one host driver without eager model startup."""
    _FakeDriver.instances.clear()
    monkeypatch.setattr(llamaCpp, "LlamaCppDriver", _FakeDriver)
    registry = SharedServiceRegistry()
    firstContext = _context("run-a")
    secondContext = _context("run-b")
    bindApplicationRunSharedServices("run-a", registry)
    bindApplicationRunSharedServices("run-b", registry)
    try:
        firstState = llamaCpp.onLoad(firstContext)
        secondState = llamaCpp.onLoad(secondContext)

        assert len(_FakeDriver.instances) == 1
        driver = _FakeDriver.instances[0]
        assert driver.startCalls == 0
        assert firstState.driver is driver
        assert secondState.driver is driver
        assert firstContext.llm.providers["llama.cpp"].driver is driver
        assert secondContext.llm.providers["llama.cpp"].driver is driver

        llamaCpp.onUnload(firstContext, firstState)
        assert driver.stopCalls == 0
        llamaCpp.onUnload(secondContext, secondState)
        assert driver.stopCalls == 1
    finally:
        unbindApplicationRunSharedServices("run-a")
        unbindApplicationRunSharedServices("run-b")
        registry.close()
