# backend/semver/semver.py
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Literal, Iterable, Generic, TypeVar



SEMVER_PATTERN_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)



T = TypeVar("T")


@total_ordering
@dataclass(frozen=True)
class SemVerPackVersion:
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
            "SemVerPackVersion("
            f"major={self.major}, minor={self.minor}, patch={self.patch}, "
            f"prerelease={self.prerelease}, build={self.build}"
            ")"
        )
    
    def _prereleaseCmpKey(self) -> tuple:
        # Numeric identifiers have lower precedence than non-numeric.
        # We encode numeric as (0, int), non-numeric as (1, str),
        # so numeric < non-numeric in tuple comparison.
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
        if not isinstance(other, SemVerPackVersion):
            return NotImplemented
        return (
            self.major == other.major
            and self.minor == other.minor
            and self.patch == other.patch
            and self.prerelease == other.prerelease
        )
    
    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVerPackVersion):
            return NotImplemented
        return self._cmpKey() < other._cmpKey()



def parseSemVerPackVersion(raw: str) -> SemVerPackVersion:
    """
    Parse a semantic version string into SemVerPackVersion.
    
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
        raise TypeError(f"Version string must be a string type, got {type(raw).__name__}")
    
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
        if idx != -1 and idx < sepIndex:
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
    
    return SemVerPackVersion(
        major=major,
        minor=minor,
        patch=patch,
        prerelease=prerelease,
        build=build
    )



@dataclass(frozen=True)
class SemVerComparator:
    operator: Literal["<", "<=", ">", ">=", "=="]
    version: SemVerPackVersion



@dataclass(frozen=True)
class SemVerPackRequirement:
    # All comparators are AND-ed.
    comparators: tuple[SemVerComparator, ...] = ()
    # If True, requirement is a wildcard ("any version")
    isAny: bool = False



def _makeComparator(op: str, versionStr: str, rawRequirement: str) -> SemVerComparator:
    if not versionStr:
        raise ValueError(f"Missing version after operator {op!r} in requirement {rawRequirement!r}")
    parsedVersion = parseSemVerPackVersion(versionStr)
    canonOp = "==" if op == "=" else op
    if canonOp not in ("<", "<=", ">", ">=", "=="):
        raise ValueError(f"Unsupported operator {op!r} in requirement {rawRequirement!r}")
    return SemVerComparator(canonOp, parsedVersion)



def _caretToComparators(version: SemVerPackVersion) -> tuple[SemVerComparator, SemVerComparator]:
    """
    ^M.m.p -> caret expansion following SemVer semantics:
    
    - If M > 0:
        >= M.m.p  and  < (M+1).0.0
    - If M == 0 and m > 0:
        >= 0.m.p  and  < 0.(m+1).0
    - If M == 0 and m == 0
        >= 0.0.p  and  < 0.0.(p+1)
    """
    Major, minor, patch = version.major, version.minor, version.patch
    greaterOrEqual = SemVerComparator(">=", version)
    if Major > 0:
        upperVersion = SemVerPackVersion(Major + 1, 0, 0)
    elif Major == 0 and minor > 0:
        upperVersion = SemVerPackVersion(0, minor + 1, 0)
    else:
        upperVersion = SemVerPackVersion(0, 0, patch + 1)
    lessThan = SemVerComparator("<", upperVersion)
    return greaterOrEqual, lessThan



def _tildeToComparators(version: SemVerPackVersion) -> tuple[SemVerComparator, SemVerComparator]:
    """
    ~M.m.p -> tilde expansion (simplified npm-ish):
    
    - If minor or patch non-zero:
        >= M.m.p  and  < M.(m+1).0
    - Else (only Major specified, e.g. '~1')
        >= M.0.0  and  < (M+1).0.0
    """
    Major, minor, patch = version.major, version.minor, version.patch
    greaterOrEqual = SemVerComparator(">=", version)
    if minor > 0 or patch > 0:
        upperVersion = SemVerPackVersion(Major, minor + 1, 0)
    else:
        upperVersion = SemVerPackVersion(Major + 1, 0, 0)
    lessThan = SemVerComparator("<", upperVersion)
    return greaterOrEqual, lessThan



def parseSemVerPackRequirement(rawVersion: str | None) -> SemVerPackRequirement | None:
    """
    Parse a requirement string into SemVerPackRequirement.
    
    Accepted forms:
    
        None, "", or "*"        -> wildcard (no constraint)
        
        "1.2.3"                 -> == 1.2.3
        ">=1.2.0"               -> >= 1.2.0
        "<2.0.0"                -> < 2.0.0
        ">=1.2.0 <2.0.0"        -> >=1.2.0 AND <2.0.0
        
        "^1.2.3"                -> >=1.2.3 AND <2.0.0 (with 0.x semantics)
        "~1.2.3"                -> >=1.2.3 AND <1.3.0
        
        "1.2.3 - 2.0.0"         -> >=1.2.3 AND <=2.0.0
        
    Tokens are separated by whitespace when not hyphen range.
    """
    if rawVersion is None:
        return None
    if not isinstance(rawVersion, str):
        raise TypeError(f"Requirement must be a string or None, got {type(rawVersion).__name__}")
    
    rawVersion = rawVersion.strip()
    if not rawVersion or rawVersion == "*":
        return None
    
    # Hyphen range: <left> - <right>, with no spaces inside version strings.
    # e.g. "1.2.3 - 2.0.0", "1 - 2.0.0", "0.1 - 0.2.3"
    mtch = re.match(r"^(?P<left>\S+)\s*-\s*(?P<right>\S+)$", rawVersion)
    comparators: list[SemVerComparator] = []
    if mtch:
        left = mtch.group("left")
        right = mtch.group("right")
        versionLeft = parseSemVerPackVersion(left)
        versionRight = parseSemVerPackVersion(right)
        if versionRight < versionLeft:
            raise ValueError(f"Invalid hyphen range {rawVersion!r}: upper < lower")
        comparators.append(SemVerComparator(">=", versionLeft))
        comparators.append(SemVerComparator("<=", versionRight))
        return SemVerPackRequirement(comparators=tuple(comparators), isAny=False)
    
    # Otherwise: parse as space-separated tokens
    tokens = rawVersion.split()
    for token in tokens:
        if not token:
            continue
        
        # Caret or tilde
        if token[0] in ("^", "~"):
            if len(token) == 1:
                raise ValueError(f"Missing version after {token[0]!r} in requirement {rawVersion!r}")
            parsedVersion = parseSemVerPackVersion(token[1:])
            if token[0] == "^":
                comps = _caretToComparators(parsedVersion)
            else:
                comps = _tildeToComparators(parsedVersion)
            comparators.extend(comps)
            continue
        
        # Relational / equality operators
        op = None
        versionPart = None
        for candidate in ("<=", ">=", "==", "<", ">", "="):
            if token.startswith(candidate):
                op = candidate
                versionPart = token[len(candidate):]
                break
        if op is not None and isinstance(versionPart, str):
            comparators.append(_makeComparator(op, versionPart, rawVersion))
            continue
        
        # Otherwise plain version -> ==version
        version = parseSemVerPackVersion(token)
        comparators.append(SemVerComparator("==", version))
    
    if not comparators:
        return None
    
    return SemVerPackRequirement(comparators=tuple(comparators), isAny=False)



def versionSatisfiesRequirement(
    version: SemVerPackVersion,
    requirement: SemVerPackRequirement | None,
) -> bool:
    """
    Checks if a version satisfies the given requirement.
    
    requirement None or isAny=True => always returns True.
    """
    if requirement is None or requirement.isAny:
        return True
    
    for comparator in requirement.comparators:
        if comparator.operator == "==":
            if not (version == comparator.version):
                return False
        elif comparator.operator == ">=":
            if not (version >= comparator.version):
                return False
        elif comparator.operator == "<=":
            if not (version <= comparator.version):
                return False
        elif comparator.operator == ">":
            if not (version > comparator.version):
                return False
        elif comparator.operator == "<":
            if not (version < comparator.version):
                return False
        else:
            raise ValueError(f"Unknown operator {comparator.operator!r}")
    return True



@dataclass(frozen=True)
class SemVerMatchResult(Generic[T]):
    """
    Result of semver-based selection among candidate versions.
    
    - requirement: the requirement used (may be None).
    - candidates: all candidates seen by the resolver.
    - matches: candidates that satisfy the requirement.
    - best: the single best match by version, or None if no matches.
            If multiple candidates share the same best version, the
            first one in the input order is returned.
    """
    requirement: SemVerPackRequirement | None
    candidates: tuple[tuple[SemVerPackVersion, T], ...]
    matches: tuple[tuple[SemVerPackVersion, T], ...]
    best: tuple[SemVerPackVersion, T] | None



class SemVerResolver:
    @staticmethod
    def matchCandidates(
        candidates: Iterable[tuple[SemVerPackVersion, T]],
        requirement: SemVerPackRequirement | None,
    ) -> SemVerMatchResult[T]:
        """
        Filter candidates by requirement and select the best version.
        
        - If requirement is None: all candidates are considered matches.
        - "Best" is the candidate with the highest SemVerPackVersion.
          If multiple candidates share the same highest version, the
          first encountered in input order is used.
        
        Returns SemVerMatchResult with:
            - candidates: all (version, payload) pairs,
            - matches: those satisfying the requirement,
            - best: best (version, payload) or None if no matches.
        """
        candidatesList: list[tuple[SemVerPackVersion, T]] = list(candidates)
        
        # Filter by requirement (or keep all if requirement is None).
        matchList: list[tuple[SemVerPackVersion, T]] = []
        for version, payload in candidatesList:
            if versionSatisfiesRequirement(version, requirement):
                matchList.append((version, payload))
        
        best: tuple[SemVerPackVersion, T] | None = None
        if matchList:
            bestVersion, bestPayload = matchList[0]
            for version, payload in matchList[1:]:
                if version > bestVersion:
                    bestVersion, bestPayload = version, payload
            best = (bestVersion, bestPayload)
        
        return SemVerMatchResult(
            requirement=requirement,
            candidates=tuple(candidatesList),
            matches=tuple(matchList),
            best=best
        )
