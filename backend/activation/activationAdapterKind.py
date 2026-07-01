# file: backend/activation/activationAdapterKind.py
from __future__ import annotations

from enum import StrEnum


class ActivationAdapterKind(StrEnum):
    PYTHON_IN_PROCESS = "python-in-process"
