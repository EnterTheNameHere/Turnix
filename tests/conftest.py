import sys
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



def pytest_configure(config: pytest.Config) -> None:
    if sys.flags.optimize:
        raise RuntimeError("Assertions are disabled (optimize > 0)")
    
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
