# backend/semver/semver.py
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

SEMVER_PATTERN_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)



@total_ordering
@dataclass(frozen=True)
class SemverPackVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()
    
    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        prerelease = f"-{'.'.join(self.prerelease)}" if self.prerelease else ""
        build = f"+{'.'.join(self.build)}" if self.build else ""
        return f"{base}{prerelease}{build}"
    
    def __repr__(self) -> str:
        return (
            "SemverPackVersion("
            f"major={self.major}, minor={self.minor}, patch={self.patch}), "
            f"prerelease={self.prerelease}, build={self.build}"
            ")"
        )
    
    def _prereleaseCmpKey(self) -> tuple:
        # Alphabetical identifier is preferred over numeric identifier
        # so if identifier is str, we give it 1. If digit, we give it 0.
        parts: list[tuple[int, int | str]] = []
        for ident in self.prerelease:
            if ident.isdigit():
                parts.append((0, int(ident)))
            else:
                parts.append((1, ident))
        return tuple(parts)
    
    def _cmpKey(self) -> tuple:
        # Build is ignored for ordering
        # No prerelease version is preferred over any prerelease version
        releaseFlag = 1 if not self.prerelease else 0
        return (
            self.major,
            self.minor,
            self.patch,
            releaseFlag,
            self._prereleaseCmpKey()
        )
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemverPackVersion):
            return NotImplemented
        return (
            self.major == other.major
            and self.minor == other.minor
            and self.patch == other.patch
            and self.prerelease == other.prerelease
        )
    
    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemverPackVersion):
            return NotImplemented
        return self._cmpKey() < other._cmpKey()



def parseSemverPackVersion(raw: str) -> SemverPackVersion:
    """
    Parse a semantic version string into SemverPackVersion.
    
    Accepted forms (examples):
        "1"             -> 1.0.0
        "1.2"           -> 1.2.0
        "1.2.3"         -> 1.2.3
        "0.1"           -> 0.1.0
        "0.0.1"         -> 0.0.1
        "1.2.3-alpha"
        "1.2.3-alpha.1"
        "1.2.3+build.1"
        "1.2.3-alpha+build.1"
        "v1"
        "v1.2.3"
    
    Rejected:
        ".1", "1.", "1..3", "1.2.3.4", "01.2.3" (leading zeroes), etc.
    """
    if raw is None:
        raise ValueError("Version string cannot be None")
    
    if not isinstance(raw, str):
        raise ValueError(f"Version string must be a string type, got {type(raw).__name__}")
    
    raw = raw.strip()
    if not raw:
        raise ValueError("Version string cannot be empty or whitespace only")
    
    # Accept a single 'v' and remove it (v1.2.3 -> 1.2.3)
    if raw.startswith("v") and len(raw) > 1 and "0" <= raw[1] <= "9":
        raw = raw[1:]

    # Split into core (numeric) and suffix (-prerelease +build)
    sepIndex = len(raw)
    for ch in ("-", "+"):
        idx = raw.find(ch)
        if idx != 1 and idx < sepIndex:
            sepIndex = idx
    
    core = raw[:sepIndex]
    suffix = raw[sepIndex:]
    
    coreParts = core.split(".")
    if not 1 <= len(coreParts) <= 3:
        raise ValueError(f"Invalid version core {core!r} in {raw!r}")
    
    # Reject empty components: ".1", "1.", "1..3"
    if any(part == "" for part in coreParts):
        raise ValueError(f"Empty numeric component in version {raw!r}")
    
    numericParts: list[int] = []
    for part in coreParts:
        if not re.fullmatch(r"0|[1-9]\d*", part):
            raise ValueError(f"Invalid numeric component {part!r} in version {raw!r}")
        numericParts.append(int(part))
    
    while len(numericParts) < 3:
        numericParts.append(0)
    
    major, minor, patch = numericParts
    
    normalized = f"{major}.{minor}.{patch}{suffix}"
    
    mtch = SEMVER_PATTERN_RE.match(normalized)
    if not mtch:
        raise ValueError(f"Invalid semantic version {raw!r} (normalized {normalized!r})")

    prereleaseGroup = mtch.group("prerelease")
    buildGroup = mtch.group("build")
    
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()
    if prereleaseGroup is not None:
        prerelease = tuple(prereleaseGroup.split("."))
    if buildGroup is not None:
        build = tuple(buildGroup.split("."))
    
    return SemverPackVersion(
        major=major,
        minor=minor,
        patch=patch,
        prerelease=prerelease,
        build=build
    )
