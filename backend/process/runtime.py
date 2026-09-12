# file: backend/process/runtime.py ; version: 1
"""Actant-owned execution boundary for configured external tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import time
from typing import Mapping, Sequence


class ProcessExecutionError(RuntimeError):
    """Reports infrastructure failure before a configured tool completed normally.

    A tool returning a nonzero exit code is not an infrastructure failure and is
    represented by :class:`ProcessResult`. This exception is reserved for
    resolution, launch, communication, timeout, or equivalent execution failure.
    """


@dataclass(frozen=True, slots=True)
class ProcessToolDefinition:
    """Binds one host-authorized logical tool name to an executable.

    The executable is runtime configuration rather than CodeEntry authority.
    Optional environment entries override the host environment inherited from
    the Actant process for invocations of this tool only.
    """

    name: str
    executable: Path
    environment: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        """Validates and freezes this configured-tool definition."""
        if type(self.name) is not str or not self.name:
            raise ValueError("Process tool name must be a non-empty string.")
        executable = Path(self.executable).expanduser()
        if not executable.is_absolute():
            raise ValueError("Process tool executable must be an absolute path.")
        object.__setattr__(self, "executable", executable)
        if self.environment is not None:
            environment = dict(self.environment)
            if any(type(key) is not str or type(value) is not str for key, value in environment.items()):
                raise TypeError("Process tool environment keys and values must be strings.")
            object.__setattr__(self, "environment", environment)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Structured evidence from one normally completed external-tool invocation."""

    toolName: str
    configuredExecutable: str
    resolvedExecutable: str
    arguments: tuple[str, ...]
    workingDirectory: str
    startedTimeNs: int
    endedTimeNs: int
    durationNs: int
    exitCode: int
    stdout: str
    stderr: str

    def snapshot(self) -> dict[str, object]:
        """Returns JSON-compatible generic execution evidence."""
        return {
            "toolName": self.toolName,
            "configuredExecutable": self.configuredExecutable,
            "resolvedExecutable": self.resolvedExecutable,
            "arguments": list(self.arguments),
            "workingDirectory": self.workingDirectory,
            "startedTimeNs": self.startedTimeNs,
            "endedTimeNs": self.endedTimeNs,
            "durationNs": self.durationNs,
            "exitCode": self.exitCode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class ProcessToolRegistry:
    """ApplicationRun-scoped registry of host-authorized external tools."""

    def __init__(self, definitions: Sequence[ProcessToolDefinition] = ()) -> None:
        """Creates a registry from unique logical tool definitions."""
        self._definitions: dict[str, ProcessToolDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ProcessToolDefinition) -> None:
        """Registers one tool definition and rejects duplicate logical names."""
        if not isinstance(definition, ProcessToolDefinition):
            raise TypeError("definition must be a ProcessToolDefinition.")
        if definition.name in self._definitions:
            raise ValueError(f"Process tool {definition.name!r} is already registered.")
        self._definitions[definition.name] = definition

    def require(self, name: str) -> ProcessToolDefinition:
        """Returns a configured tool or raises when no such authority exists."""
        if type(name) is not str or not name:
            raise ValueError("Process tool name must be a non-empty string.")
        try:
            return self._definitions[name]
        except KeyError as err:
            raise ProcessExecutionError(f"Process tool {name!r} is not configured.") from err


class ProcessRunner:
    """Executes configured tools without granting arbitrary subprocess authority."""

    def __init__(self, registry: ProcessToolRegistry) -> None:
        """Binds execution to one host-authorized tool registry."""
        if not isinstance(registry, ProcessToolRegistry):
            raise TypeError("registry must be a ProcessToolRegistry.")
        self._registry = registry

    def run(
        self,
        toolName: str,
        arguments: Sequence[str] = (),
        *,
        workingDirectory: str | Path | None = None,
        timeoutSeconds: float | None = None,
    ) -> ProcessResult:
        """Runs one configured tool and captures its complete textual result.

        A nonzero tool exit is a normal completed invocation and is returned in
        ``ProcessResult``. Failure to resolve, launch, communicate with, or wait
        for the process is an Actant infrastructure failure.

        Args:
            toolName: Logical host-configured tool identity.
            arguments: Exact argument vector following the executable.
            workingDirectory: Existing directory used as the child process cwd.
            timeoutSeconds: Optional positive completion timeout.

        Returns:
            Structured generic execution evidence.

        Raises:
            ProcessExecutionError: If the configured tool cannot complete an
                ordinary invocation.
            TypeError: If arguments or timeout have invalid types.
            ValueError: If the working directory or timeout value is invalid.
        """
        definition = self._registry.require(toolName)
        normalizedArguments = self._normalizeArguments(arguments)
        cwd = Path.cwd() if workingDirectory is None else Path(workingDirectory).expanduser()
        try:
            cwd = cwd.resolve(strict=True)
        except OSError as err:
            raise ProcessExecutionError(f"Process working directory cannot be resolved: {cwd}") from err
        if not cwd.is_dir():
            raise ValueError(f"Process working directory is not a directory: {cwd}")
        if timeoutSeconds is not None:
            if type(timeoutSeconds) not in (int, float):
                raise TypeError("timeoutSeconds must be a number or null.")
            if timeoutSeconds <= 0:
                raise ValueError("timeoutSeconds must be positive.")

        configuredExecutable = definition.executable
        try:
            resolvedExecutable = configuredExecutable.resolve(strict=True)
        except OSError as err:
            raise ProcessExecutionError(
                f"Configured executable for process tool {toolName!r} cannot be resolved: {configuredExecutable}",
            ) from err
        if not resolvedExecutable.is_file():
            raise ProcessExecutionError(
                f"Configured executable for process tool {toolName!r} is not a file: {resolvedExecutable}",
            )

        environment = os.environ.copy()
        if definition.environment is not None:
            environment.update(definition.environment)
        startedTimeNs = time.time_ns()
        startedMonotonicNs = time.monotonic_ns()
        try:
            completed = subprocess.run(
                [str(resolvedExecutable), *normalizedArguments],
                cwd=str(cwd),
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeoutSeconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as err:
            raise ProcessExecutionError(f"Process tool {toolName!r} failed to execute: {err}") from err
        endedMonotonicNs = time.monotonic_ns()
        endedTimeNs = time.time_ns()
        return ProcessResult(
            toolName=toolName,
            configuredExecutable=str(configuredExecutable),
            resolvedExecutable=str(resolvedExecutable),
            arguments=normalizedArguments,
            workingDirectory=str(cwd),
            startedTimeNs=startedTimeNs,
            endedTimeNs=endedTimeNs,
            durationNs=endedMonotonicNs - startedMonotonicNs,
            exitCode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    @staticmethod
    def _normalizeArguments(arguments: Sequence[str]) -> tuple[str, ...]:
        """Validates and freezes an exact child-process argument vector."""
        if isinstance(arguments, (str, bytes)):
            raise TypeError("Process arguments must be a sequence of strings, not one string.")
        normalized = tuple(arguments)
        if any(type(argument) is not str for argument in normalized):
            raise TypeError("Every process argument must be a string.")
        if any("\x00" in argument for argument in normalized):
            raise ValueError("Process arguments cannot contain NUL characters.")
        return normalized
