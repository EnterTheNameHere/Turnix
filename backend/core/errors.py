# file: backend/core/errors.py
from __future__ import annotations


class ActantError(Exception):
    """
    Base class for trusted Actant platform implementation errors.
    """


class UsageError(ActantError):
    """
    Raised when a command or caller supplies invalid input.
    """


class InternalError(ActantError):
    """
    Raised when trusted implementation code detects an internal invariant
    failure.
    """
