from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class RepoRootNotFoundError(RuntimeError):
    """Raised when the Milestone 2 repo-only locator cannot identify the repository root."""


@dataclass(frozen=True)
class RuntimeRoots:
    """Repository-local roots used by the Milestone 2 RuntimeHost skeleton."""

    repo: Path
    backend: Path
    custom: Path
    firstParty: Path
    thirdParty: Path
    userdata: Path
    saves: Path
    docs: Path
    design: Path
    pythonEmbedded: Path
    vendor: Path

    @property
    def contentRoots(self) -> tuple[Path, Path, Path]:
        """Returns pack-hosting roots in Turnix resolution priority order."""
        return (self.custom, self.firstParty, self.thirdParty)

    @property
    def runtimeVisibleRoots(self) -> tuple[Path, Path, Path, Path, Path]:
        """Returns roots that are visible to runtime root handling."""
        return (self.custom, self.firstParty, self.thirdParty, self.userdata, self.saves)


class RepoOnlyRootLocator:
    """
    Locates Turnix roots only inside the repository directory.

    This is an explicit Milestone 2 cheat. The future locator can add CLI, environment, userdata
    redirect, and OS-directory lookup without changing RuntimeHost callers.
    """

    _REPO_MARKERS: tuple[str, ...] = ("backend", "first-party")

    def locate(self, startPath: Path | str | None = None) -> RuntimeRoots:
        repoRoot = self.findRepoRoot(startPath)
        return RuntimeRoots(
            repo=repoRoot,
            backend=repoRoot / "backend",
            custom=repoRoot / "custom",
            firstParty=repoRoot / "first-party",
            thirdParty=repoRoot / "third-party",
            userdata=repoRoot / "userdata",
            saves=repoRoot / "saves",
            docs=repoRoot / "docs",
            design=repoRoot / "design",
            pythonEmbedded=repoRoot / "python-embedded",
            vendor=repoRoot / "vendor",
        )

    def findRepoRoot(self, startPath: Path | str | None = None) -> Path:
        """Finds the nearest parent that looks like the Turnix repository root."""
        candidates = self._candidateRoots(startPath)
        for candidate in candidates:
            if self._isRepoRoot(candidate):
                return candidate.resolve()

        searched = ", ".join(str(path) for path in candidates)
        raise RepoRootNotFoundError(f"Turnix repository root was not found. Searched: {searched}")

    def ensureRuntimeDirectories(self, roots: RuntimeRoots) -> None:
        """Creates missing repo-local runtime directories used by the M2 skeleton."""
        for path in roots.runtimeVisibleRoots:
            path.mkdir(parents=True, exist_ok=True)

    def _candidateRoots(self, startPath: Path | str | None) -> list[Path]:
        starts: list[Path] = []
        if startPath is not None:
            starts.append(Path(startPath))
        starts.append(Path.cwd())
        starts.append(Path(__file__).resolve())

        candidates: list[Path] = []
        for start in starts:
            current = start.resolve()
            if current.is_file():
                current = current.parent
            candidates.extend([current, *current.parents])

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key not in seen:
                deduped.append(candidate)
                seen.add(key)
        return deduped

    def _isRepoRoot(self, path: Path) -> bool:
        return all((path / marker).exists() for marker in self._REPO_MARKERS)
