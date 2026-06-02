# backend/runtime/workspace.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from backend.runtime.roots import RuntimeRoots


@dataclass(frozen=True)
class RuntimeWorkspace:
    """Minimal runtime workspace acquired by the Milestone 2 RuntimeHost skeleton."""

    roots: RuntimeRoots

    @property
    def repoRoot(self) -> Path:
        return self.roots.repo
