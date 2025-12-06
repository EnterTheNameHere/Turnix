# backend/content/roots.py
from __future__ import annotations
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from backend.app.context import PROCESS_REGISTRY
from backend.app.globals import getTracer
from backend.core.errors import ReactorScramError

__all__ = [
    "RootKind",
    "RootSet",
    "ContentRootsService",
    "defaultUserRoot",
    "ROOT_DIR",
    "WEB_ROOT",
    "DEFAULT_FIRST_PARTY_DIR",
    "DEFAULT_THIRD_PARTY_DIR",
]

# ------------------------------------------------------------------ #
# Static paths (repo layout)
# ------------------------------------------------------------------ #

ROOT_DIR = Path(__file__).resolve().parent.parent.parent # repository root
WEB_ROOT = ROOT_DIR / "frontend"

# Subfolder kinds under a Turnix "root":
RootKind = Literal["first-party", "third-party", "custom", "userdata", "saves"]
_ROOT_KINDS_TO_ATTR: dict[str, str] = {
    "first-party": "firstParty",
    "third-party": "thirdParty",
    "custom": "custom",
    "userdata": "userdata",
    "saves": "saves"
}
_REQUIRED_SUBDIRS: tuple[str, ...] = tuple(_ROOT_KINDS_TO_ATTR.keys())

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()

def _mkDir(path: Path) -> None:
    """
    Create a directory (parents included). Errors bubble up to explicit callers (writing).
    """
    path.mkdir(parents=True, exist_ok=True)

def _isWindows() -> bool:
    return platform.system().lower().startswith("win")

def _hasAllSubdirs(base: Path) -> bool:
    try:
        return all((base / name).is_dir() for name in _REQUIRED_SUBDIRS)
    except Exception:
        return False

def _safeSameFile(first: Path, second: Path) -> bool:
    """
    Robust same-file check that tolerates non-existent paths and platform quirks.
    """
    try:
        if first.exists() and second.exists():
            return first.samefile(second)
    except Exception:
        # Fallback to normalized absolute path string compare
        pass
    try:
        return str(first.resolve()) == str(second.resolve())
    except Exception:
        return str(first) == str(second)

def _dedupePaths(paths: list[Path]) -> list[Path]:
    """
    Deduplicate path list in order, using safe same-file semantics.
    """
    out: list[Path] = []
    for first in paths:
        if not any(_safeSameFile(first, second) for second in out):
            out.append(first)
    return out

# ------------------------------------------------------------------ #
# Root model
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class RootSet:
    """
    One Turnix "root" base with canonical subdirectories.
    - Existence of subdirectories is *not* guaranteed.
    - If CLI --root is used, non-existing directories *will* be created.
    - Repo root must contain all the directories or error is raised.
    """
    base: Path
    firstParty: Path
    thirdParty: Path
    custom: Path
    userdata: Path
    saves: Path
    priority: int = 0      # Higher value wins in resolution
    label: str = "default" # For diagnostics
    
    def subdir(self, kind: RootKind) -> Path:
        return getattr(self, _ROOT_KINDS_TO_ATTR[kind])



def _declareRoot(base: Path | str, *, priority: int, label: str, createDirectories: bool = False) -> RootSet:
    """
    createDirectories=True should only be used for CLI --root to create base + all 5 subdirectories.
    Other roots are NOT supposed to create anything here.
    """
    base = _resolve(base)
    firstParty = base / "first-party"
    thirdParty = base / "third-party"
    custom = base / "custom"
    userdata = base / "userdata"
    saves = base / "saves"
    
    if createDirectories:
        # CLI --root must exist and be ready if used
        for path in (base, firstParty, thirdParty, userdata, saves, custom):
            _mkDir(path)

    return RootSet(
        base=base,
        firstParty=firstParty,
        thirdParty=thirdParty,
        custom=custom,
        userdata=userdata,
        saves=saves,
        priority=priority,
        label=label,
    )

# ------------------------------------------------------------------ #
# Service
# ------------------------------------------------------------------ #

@dataclass
class ContentRootsService:
    """
    Centralized roots + read/write resolution with strict rules:
    
    Reading order:
      1) CLI --root (if doesn't exist, will be created - with all subdirectories)
      2) env TURNIX_ROOT (if base exists, do not create)
      3) OS user dirs (only include existing, do not create)
      4) repo root (LAST) - must have all file subdirectories or raises error
    
    Overrides:
      - CLI --userdata / --saves - highest priority if set (reading) and forced for writing.
      - Preferred write base can be selected for userdata/saves. Defaults to repo root.
      - CLI overrides beat preferences and config.
    """
    # Discovered roots in final priority order (desc priority, then stable path)
    _roots: list[RootSet] = field(default_factory=list)
    
    # Optional explicit write overrides (CLI flags) - single directories, not bases
    _cliUserdata: Path | None = None
    _cliSaves: Path | None = None
    
    # Optional preferred write bases (chosen in UI / config) - bases (the directory that contains subdirs)
    _preferredUserdataBase: Path | None = None
    _preferredSavesBase: Path | None = None
    
    # ----- Construction -----
    
    @classmethod
    def build(
        cls,
        *,
        cliRoot: str | None = None,
        cliUserdata: str | None = None,
        cliSaves: str | None = None,
    ) -> "ContentRootsService":
        """
        Constructs the service per specified ordering and policies.
        """
        tracer = getTracer()
        span = None
        try:
            span = tracer.startSpan(
                "roots.build",
                attrs={
                    "cliRoot": cliRoot or "",
                    "cliUserdata": cliUserdata or "",
                    "cliSaves": cliSaves or "",
                    "platform": platform.system(),
                },
                tags=["roots"],
            )
            tracer.traceEvent(
                "roots.build.start",
                level="info",
                tags=["roots"],
                span=span,
            )
        except Exception:
            span = None
        
        try:
            roots: list[RootSet] = []
            
            # 1) CLI --root (must exist - if not, create)
            if cliRoot:
                rootSet = _declareRoot(cliRoot, priority=1000, label="cli", createDirectories=True)
                roots.append(rootSet)

            # 2) env TURNIX_ROOT (only if the base exists)
            envRoot = os.getenv("TURNIX_ROOT")
            if envRoot:
                base = Path(envRoot).expanduser()
                if base.exists():
                    roots.append(_declareRoot(base, priority=900, label="env", createDirectories=False))
            
            # 3) OS user dirs (only if exists)
            if _isWindows():
                # a) My Games
                # Try to locate "Documents\My Games\Turnix". If Documents was moved, USERPROFILE-based fallback.
                userprofile = os.getenv("USERPROFILE")
                docs = None
                if userprofile:
                    docs = Path(userprofile).expanduser() / "Documents"
                if docs is None or not docs.exists():
                    docs = Path.home() / "Documents"
                myGamesBase = docs / "My Games" / "Turnix"
                if myGamesBase.exists():
                    roots.append(_declareRoot(myGamesBase, priority=800, label="my-games", createDirectories=False))
                
                # b) Roaming
                roaming = os.getenv("APPDATA")
                if roaming:
                    appdataBase = Path(roaming).expanduser() / "Turnix"
                    if appdataBase.exists():
                        roots.append(_declareRoot(appdataBase, priority=700, label="appdata", createDirectories=False))
            else:
                # a) XDG data (~/.local/share/turnix or $XDG_DATA_HOME/turnix)
                xdgDataHome = os.getenv("XDG_DATA_HOME")
                xdgDataBase = Path(xdgDataHome).expanduser() if xdgDataHome else Path.home() / ".local" / "share" / "turnix"
                if xdgDataBase.exists():
                    roots.append(_declareRoot(xdgDataBase, priority=800, label="xdg-data", createDirectories=False))
                
                # b) XDG config (~/.config/turnix or $XDG_CONFIG_HOME/turnix)
                xdgCfg = os.getenv("XDG_CONFIG_HOME")
                xdgCfgBase = Path(xdgCfg).expanduser() if xdgCfg else Path.home() / ".config" / "turnix"
                if xdgCfgBase.exists():
                    roots.append(_declareRoot(xdgCfgBase, priority=700, label="xdg-config", createDirectories=False))

            # 4) Repo root (MUST be last, MUST have all five subdirectories or raises error)
            repoBase = ROOT_DIR
            if not _hasAllSubdirs(repoBase):
                raise ReactorScramError(
                    "“Success begins with structure.”\n"
                    "Your directory structure: Not Found.\n"
                    f"Needed: {', '.join(_ROOT_KINDS_TO_ATTR.keys())} under '{repoBase}'.\n"
                    "Turnix has seized up while reconsidering life goals. Please build folders, achieve greatness, "
                    "avoid meltdown. Redownload Turnix?"
                )
            roots.append(_declareRoot(repoBase, priority=100, label="repo", createDirectories=False))
            
            # Sort by priority desc, then by base for determinism. (We also override below)
            roots.sort(key=lambda root: (-root.priority, str(root.base)))
            
            service = cls(_roots=roots)
            if cliUserdata:
                service.setCliOverride("userdata", cliUserdata)
            if cliSaves:
                service.setCliOverride("saves", cliSaves)
            
            if span is not None:
                try:
                    tracer.traceEvent(
                        "roots.build.done",
                        level="info",
                        tags=["roots"],
                        span=span,
                        attrs={
                            "rootCount": len(service._roots),
                            "labels": [root.label for root in service._roots],
                        },
                    )
                    tracer.endSpan(
                        span,
                        status="ok",
                        tags=["roots"],
                    )
                except Exception:
                    pass
            
            return service
        
        except Exception as err:
            if span is not None:
                try:
                    tracer.traceEvent(
                        "roots.build.error",
                        level="error",
                        tags=["roots", "error"],
                        span=span,
                        attrs={
                            "errorType": type(err).__name__,
                            "errorMessage": str(err),
                        },
                    )
                    tracer.endSpan(
                        span,
                        status="error",
                        tags=["roots", "error"],
                        errorType=type(err).__name__,
                        errorMessage=str(err),
                    )
                except Exception:
                    pass
            
            raise
    
    # ----- Overrides / preferences -----
    
    def setCliOverride(self, kind: Literal["userdata", "saves"], path: Path | str) -> None:
        """
        CLI --userdata/--saves: highest priority for reading, forced for writing. Created on write.
        Defensive: ignore empty paths. Normalize on set.
        """
        target = _resolve(path)
        if kind == "userdata":
            self._cliUserdata = target
        elif kind == "saves":
            self._cliSaves = target
    
    def setPreferredWriteBase(self, kind: Literal["userdata", "saves"], baseDir: Path | str | None) -> None:
        """
        UI/config-selected write base (directory that *contains* the subdir).
        Example: user selects 'My Games/Turnix' base → writing goes to base/<kind>.
        """
        if baseDir is None:
            if kind == "userdata":
                self._preferredUserdataBase = None
            elif kind == "saves":
                self._preferredSavesBase = None
            return
        
        base = _resolve(baseDir)
        if kind == "userdata":
            self._preferredUserdataBase = base
        elif kind == "saves":
            self._preferredSavesBase = base
    
    def preferWriteBaseByLabel(self, kind: Literal["userdata", "saves"], label: str) -> bool:
        """
        Convenience for UI: choose preferred base by known label ("repo", "my-games", …).
        Returns True on success, False if label not found.
        """
        for rootSet in self._roots:
            if rootSet.label == label:
                self.setPreferredWriteBase(kind, rootSet.base)
                return True
        return False
    
    # ----- Reading: lists of dirs to scan -----
    
    def rootsFor(self, kind: RootKind) -> list[Path]:
        """
        Reading list for the requested kind, respecting:
          - CLI overrides (first for their kind)
          - Only existing directories are returned (no creation)
          - Repo root dir (as last) is guaranteed to exist (validated at build).
        """
        out: list[Path] = []
        
        # Per-kind CLI override first (if any) and exists
        if kind == "userdata" and self._cliUserdata and self._cliUserdata.exists():
            out.append(self._cliUserdata)
        elif kind == "saves" and self._cliSaves and self._cliSaves.exists():
            out.append(self._cliSaves)
        
        # Then every discovered root's subdir for the kind, only if it exists
        for rootSet in self._roots:
            sub = rootSet.subdir(kind)
            if sub.exists():
                out.append(sub)
        
        return _dedupePaths(out)
    
    def contentRoots(self) -> list[Path]:
        """
        Reading list of content-hosting roots (first-party/third-party/custom), existing-only, in priority order.
        """
        out: list[Path] = []
        for rootSet in self._roots:
            for sub in (rootSet.firstParty, rootSet.thirdParty, rootSet.custom):
                if sub.exists():
                    out.append(sub)
        return _dedupePaths(out)

    def packRoots(self) -> list[Path]:
        """
        Alias for discovery callers that operate on pack-hosting roots.
        """
        return self.contentRoots()
    
    # ----- Writing: choose/create the directory -----
    
    def getWriteDir(self, kind: Literal["userdata", "saves"]) -> Path:
        """
        Returns the chosen directory for writing (creates it if missing), using:
          1) CLI override if present (wins over everything)
          2) Preferred write base (set through UI/config), if set
          3) Repo root (default)
        """
        source = "repo"
        
        # CLI override wins
        if kind == "userdata" and self._cliUserdata:
            _mkDir(self._cliUserdata)
            chosen = self._cliUserdata
            source = "cli"
        elif kind == "saves" and self._cliSaves:
            _mkDir(self._cliSaves)
            chosen = self._cliSaves
            source = "cli"
        else:
            # Preferred base → map to its subdir for the kind
            basePref = self._preferredUserdataBase if kind == "userdata" else self._preferredSavesBase
            if basePref:
                sub = _resolve(basePref) / kind
                _mkDir(sub)
                chosen = sub
                source = "preferred"
            else:
                # Default = repo root subdir
                repo = next((root for root in self._roots if root.label == "repo"), None)
                if not repo:
                    raise ReactorScramError(
                        "Repo root not found.\n"
                        "Tried CLI, env vars, roaming, My Games, prayer.exe.\n"
                        "If this is truly happening, either storage snapped or reality did.\n"
                        "Set repo root and press F to continue.\n"
                        "Actually - do not press F.\n"
                        "I repeat - DO NOT PRESS F.\n"
                        "But try reinstalling Turnix, maybe..."
                    )
                sub = getattr(repo, kind)
                _mkDir(sub)
                chosen = sub
        
        try:
            tracer = getTracer()
            tracer.traceEvent(
                "roots.writeDir",
                level="debug",
                tags=["roots"],
                attrs={
                    "kind": kind,
                    "path": str(chosen),
                    "source": source,
                },
            )
        except Exception:
            pass
        
        return chosen
    
    def listWriteCandidates(self, kind: Literal["userdata", "saves"]) -> list[tuple[str, Path, bool]]:
        """
        Lists viable write base candidates (label, basePath, existsFlag) in priority order.
        Does not create anything. Helpful for UI pickers.
        
        Note: For CLI override (if set), returns (label='cli-override', path=<dir>, exists).
        """
        out: list[tuple[str, Path, bool]] = []
        
        # CLI override as a direct dir (top)
        if kind == "userdata" and self._cliUserdata:
            out.append(("cli-override", self._cliUserdata, self._cliUserdata.exists()))
        if kind == "saves" and self._cliSaves:
            out.append(("cli-override", self._cliSaves, self._cliSaves.exists()))
        
        # Discovered roots (show base; actual write goes to base/<kind>)
        for rootSet in self._roots:
            out.append((rootSet.label, rootSet.base, rootSet.base.exists()))
        
        # Stable, deduped by base
        deduped: list[tuple[str, Path, bool]] = []
        seen: list[Path] = []
        for label, base, exists in out:
            if not any(_safeSameFile(base, bb) for bb in seen):
                deduped.append((label, base, exists))
                seen.append(base)
        return deduped

# ------------------------------------------------------------------ #
# Convenience exports
# ------------------------------------------------------------------ #

DEFAULT_FIRST_PARTY_DIR = _resolve(ROOT_DIR / "first-party")
DEFAULT_THIRD_PARTY_DIR = _resolve(ROOT_DIR / "third-party")

def defaultUserRoot() -> Path:
    """
    Default write base (for UI) is the repo root. Writing goes into repo/<kind>.
    """
    return _resolve(ROOT_DIR)

# ------------------------------------------------------------------ #
# Initialization
# ------------------------------------------------------------------ #

def initRoots(*, cliRoot: str | None = None, cliUserdata: str | None = None, cliSaves: str | None = None):
    PROCESS_REGISTRY.register("roots.service", ContentRootsService.build(
        cliRoot=cliRoot,
        cliUserdata=cliUserdata,
        cliSaves=cliSaves,
    ))
