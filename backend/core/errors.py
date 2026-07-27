# file: backend/core/errors.py ; version: 1
from __future__ import annotations


class ActantError(Exception):
    """
    Base class for exceptions intentionally defined by Actant.

    Domain-specific exceptions hierarchies derive from this class. Catching
    ActantError indicates that the caller can handle any declared Actant
    exception without requiring domain-specific interpretation.
    """


class CoreError(ActantError):
    """Base class for exceptions owned by Actant core infrastructure."""


class CoreInvariantError(ActantError):
    """
    Raised when trusted core code detects an impossible internal state.

    This indicates an implementation defect or corrupted internal state, not
    invalid caller or user input.
    """
