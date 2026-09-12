# file: backend/process/configuration.py ; version: 1
"""Construction of host-authorized process tools from runtime configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from backend.process.runtime import ProcessToolDefinition


def processToolDefinitionsFromConfig(config: Mapping[str, object] | None) -> tuple[ProcessToolDefinition, ...]:
    """Builds configured process-tool authority from runtime host configuration.

    The optional ``processTools`` object maps logical names such as ``ruff`` or
    ``ty`` to absolute executable paths. The mapping is host/runtime policy;
    CodeEntry code receives only the logical names and cannot replace the
    executable for an invocation.

    Args:
        config: Runtime configuration mapping, or ``None`` for no configured
            tools.

    Returns:
        Immutable process-tool definitions suitable for an ApplicationRun
        registry.

    Raises:
        TypeError: If ``config`` or ``processTools`` has the wrong structural
            type, or a tool path is not a string.
        ValueError: If a logical tool name is invalid or its executable path is
            not absolute.
    """
    if config is None:
        return ()
    if not isinstance(config, Mapping):
        raise TypeError("Runtime process configuration must be a mapping or null.")
    rawTools = config.get("processTools")
    if rawTools is None:
        return ()
    if not isinstance(rawTools, Mapping):
        raise TypeError("Runtime processTools configuration must be an object.")

    definitions: list[ProcessToolDefinition] = []
    for name, executable in rawTools.items():
        if type(name) is not str or not name:
            raise ValueError("Runtime processTools names must be non-empty strings.")
        if type(executable) is not str:
            raise TypeError(f"Runtime process tool {name!r} executable must be a string.")
        definitions.append(
            ProcessToolDefinition(
                name=name,
                executable=Path(executable),
            ),
        )
    return tuple(definitions)
