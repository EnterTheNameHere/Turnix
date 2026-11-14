# file: backend/core/errors.py ; version: 2
from __future__ import annotations


class PlatformError(Exception):
    """
    Base class for exceptions intentionally defined by Tertalis.

    Domain-specific exceptions hierarchies derive from this class. Catching
    PlatformError indicates that the caller can handle any declared Tertalis
    exception without requiring domain-specific interpretation.
    """


class CoreError(PlatformError):
    """Base class for exceptions owned by Tertalis core infrastructure."""


class CoreInvariantError(PlatformError):
    """
    Raised when trusted core code detects an impossible internal state.

    This indicates an implementation defect or corrupted internal state, not
    invalid caller or user input.
    """
