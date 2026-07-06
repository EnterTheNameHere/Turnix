# file: backend/core/validation.py
from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from backend.core.errors import UsageError


def typeName(value: object) -> str:
    """Helper to prevent TypeError("Expected list, got {type(value)}.__name__.") typo."""
    return type(value).__name__


def requireExactNonBlankString(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise UsageError(f"{name} must be a string, not {typeName(value)}.")

    if value == "":
        raise UsageError(f"{name} must not be an empty string.")

    strippedValue = value.strip()

    if strippedValue == "":
        raise UsageError(f"{name} must not be a string containing only whitespace.")

    if value != strippedValue:
        raise UsageError(f"{name} must not contain leading or trailing whitespace.")

    return value


def requireMapping(value: object, name: str) -> Mapping[Any, Any]:
    if not isinstance(value, Mapping):
        raise UsageError(f"{name} must be an object, not {typeName(value)}.")
    return value


def requireOptionalString(value: object, name: str) -> str | None:
    if value is None:
        return None
    return requireExactNonBlankString(value, name)


def requireRelativePurePosixPath(value: object, name: str) -> PurePosixPath:
    if isinstance(value, str):
        path = PurePosixPath(value)
    elif isinstance(value, PurePosixPath):
        path = value
    else:
        raise UsageError(f"{name} must be a relative POSIX path string, not {typeName(value)}.")

    text = path.as_posix()

    if text == ".":
        raise UsageError(f"{name} must not be empty.")

    if path.is_absolute():
        raise UsageError(f"{name} must be relative: {text}.")

    if "\\" in text:
        raise UsageError(f"{name} must use POSIX '/' separators, not backslashes: {text}.")

    if ".." in path.parts:
        raise UsageError(f"{name} must not contain '..': {text}.")

    if any(part != part.strip() for part in path.parts):
        raise UsageError(f"{name} path segments must not contain leading or trailing whitespace: {text}.")

    return path
