import asyncio
from collections.abc import Awaitable, Callable

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "asyncio_mode",
        "Execution mode for @pytest.mark.asyncio tests (only 'strict' is supported without pytest-asyncio).",
        default="strict",
    )
    parser.addini(
        "asyncio_default_fixture_loop_scope",
        "Scope for the event loop fixture (only 'function' is supported without pytest-asyncio).",
        default="function",
    )


def _run_async(func: Callable[..., Awaitable], *args, **kwargs):
    """Run the given coroutine function to completion."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(func(*args, **kwargs))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def pytest_configure(config: pytest.Config) -> None:
    mode = config.getini("asyncio_mode")
    if mode != "strict":
        raise pytest.UsageError(
            "tests/conftest.py only supports asyncio_mode='strict' without pytest-asyncio installed"
        )

    loop_scope = config.getini("asyncio_default_fixture_loop_scope")
    if loop_scope != "function":
        raise pytest.UsageError(
            "tests/conftest.py only supports asyncio_default_fixture_loop_scope='function'"
        )

    config.addinivalue_line("markers", "asyncio: mark a test to run inside an event loop")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool:
    marker = pyfuncitem.get_closest_marker("asyncio")
    if marker is None:
        return False

    func = pyfuncitem.obj
    if not asyncio.iscoroutinefunction(func):
        return False

    kwargs = {arg: pyfuncitem.funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames}
    _run_async(func, **kwargs)
    return True
