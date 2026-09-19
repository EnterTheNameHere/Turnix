# file: tests/first_party/llmDrivers/test_llamaCppCancellation.py ; version: 1
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from backend.llm.llmTypes import LlmCallRequest, LlmQuery, LlmStreamEvent
from backend.orchestration import CancellationSignal

if TYPE_CHECKING:
    from collections.abc import Iterator
    import pytest


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "llmDrivers"
    / "llamaCpp"
    / "structuredCodeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("llamaCppCancellationTest", _CODE_ENTRY)
assert _SPEC is not None
assert _SPEC.loader is not None
llamaCpp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(llamaCpp)


class _Driver:
    """Minimal driver surface required by the structured llama.cpp stream adapter."""

    manageServer = True
    baseUrl = "http://127.0.0.1:8080"

    def ensureModel(self, model: str | None) -> SimpleNamespace:
        """Returns one deterministic selected-model record."""
        return SimpleNamespace(name="model-a" if model is None else model)


class _Response:
    """Context-managed fake HTTP response with observable close behavior."""

    def __init__(self) -> None:
        """Creates one open fake response."""
        self.closeCalls = 0

    def close(self) -> None:
        """Records provider execution-stop attempts."""
        self.closeCalls += 1

    def __enter__(self) -> _Response:
        """Returns this response as its context value."""
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Closes the response when provider streaming exits."""
        self.close()


def test_llama_cpp_cancellation_closes_active_http_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider realizes generic cancellation by closing only its active response."""
    response = _Response()
    signal = CancellationSignal()

    def openResponse(*_args: object, **_kwargs: object) -> _Response:
        """Returns the deterministic fake provider response."""
        return response

    def readEvents(_response: object) -> Iterator[LlmStreamEvent]:
        """Requests cancellation while the provider response is active."""
        yield LlmStreamEvent(eventType="delta", text="partial")
        assert signal.request() is True
        yield LlmStreamEvent(eventType="completed")

    monkeypatch.setattr(llamaCpp.urlRequest, "urlopen", openResponse)
    monkeypatch.setattr(llamaCpp._impl, "_readEvents", readEvents)

    provider = llamaCpp.LlamaCppStreamProvider(driver=_Driver())
    request = LlmCallRequest(
        query=LlmQuery(formatId="text/plain", payload="question"),
        cancellationSignal=signal,
    )

    events = list(provider.stream(request))

    assert [event.eventType for event in events] == ["delta", "completed"]
    assert response.closeCalls >= 1
