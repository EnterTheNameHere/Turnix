# backend/runtime/workspace.py
from __future__ import annotations

from dataclasses import dataclass

from backend.runtime.roots import RuntimeRoots


@dataclass(frozen=True)
class RuntimeWorkspace:
    """Minimal runtime workspace acquired by the Milestone 2 RuntimeHost skeleton."""

    roots: RuntimeRoots

    @property
    def repoRoot(self):
        return self.roots.repo
