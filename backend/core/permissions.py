# backend/core/permissions.py

from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from semantic_version import Version, NpmSpec

Decision = Literal["allow", "deny"]

@dataclass(frozen=True)
class GrantPermission:
    principal: str   # e.g. modId, "system", "devtools"
    family: str      # capability family, e.g. "http.client", "chat"
    rangeExpr: str = "*" # semver range (npm style); e.g. "^1", "~1.2", ">=1.4 <2"
    decision: Decision = "deny"
    scope: dict[str, Any] | None = None # Optional: { "hosts": ["api.example.com"] }
    expiresAtMs: int | None = None      # None => no expiry

def nowMs() -> int:
    import time as _t
    return int(_t.time() * 1000)

def isExpired(grantPerm: GrantPermission) -> bool:
    return grantPerm.expiresAtMs is not None and nowMs() > grantPerm.expiresAtMs

def parseCapability(capStr: str) -> tuple[str, Version | None]:
    """
    "chat@1.5.2" -> ("chat", Version("1.5.2"))
    "http.client@1" -> ("http.client", Version("1.0.0"))
    "chat" -> ("chat", None)
    """
    capStr = (capStr or "").strip()

    if "@" not in capStr:
        return capStr, None
    
    family, verStr = capStr.split("@", 1)
    verStr = verStr.strip()

    if verStr.count(".") == 0: # "1" -> "1.0.0"
        verStr = f"{verStr}.0.0"
    elif verStr.count(".") == 1: # "1.2" -> "1.2.0"
        verStr = f"{verStr}.0"

    try:
        return family, Version(verStr, partial=False)
    except ValueError:
        # Treat invalid as no-version → will fail a strict check if required
        return family, None

@lru_cache(maxsize=256)
def _compileSpec(expr: str) -> NpmSpec:
    expr = (expr or "*").strip() or "*"
    return NpmSpec(expr)

def versionInRange(version: Version, rangeExpr: str) -> bool:
    try:
        return _compileSpec(rangeExpr).match(version)
    except ValueError:
        return False

class GrantPermissionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, extra: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.extra = extra or {}

class PermissionManager:
    def __init__(self):
        # key: (principal, family) -> GrantPermission
        self._grants: dict[tuple[str, str], GrantPermission] = {}
        # Capability metadata
        # family -> { "serverRange": str, "risk": "low"|"medium"|"high" }
        self._capsMeta: dict[str, dict[str, Any]] = {}
    
    def registerCapability(self, *, capability: str, serverVersion: str, risk: str = "low") -> None:
        """
        capability: "chat@1" or "http.client@1"
        serverVersion: "0.2.3", "0.4.5", etc.
        risk: "low"|"medium"|"high" - influences prompting policy
        """
        family, ver = parseCapability(capability)
        if ver is None:
            # Fallback to registered serverVersion
            _, normalizedVer = parseCapability(f"{family}@{serverVersion}")
            ver = normalizedVer
        serverRange = f"^{ver.major}" if ver else "*"
        self._capsMeta[family] = {
            "serverRange": serverRange,
            "risk": risk,
        }
        
    # ---------- Granting management ----------
    def putGrant(self, grant: GrantPermission) -> None:
        self._grants[(grant.principal, grant.family)] = grant
    
    def revokeGrant(self, principal: str, family: str) -> None:
        self._grants.pop((principal, family), None)
    
    def getGrant(self, principal: str, family: str) -> GrantPermission | None:
        grant = self._grants.get((principal, family))
        if grant and isExpired(grant):
            self._grants.pop((principal, family), None)
            return None
        return grant
    
    # ---------- Enforcement ----------
    def ensure(self, *, principal: str, capability: str) -> None:
        """
        Raises PermissionError if not allowed.
        """
        family, reqVer = parseCapability(capability)
        if not family:
            raise GrantPermissionError("PERMISSION_INVALID", "Empty capability", retryable=False)

        # Server baseline: if the server registered a family, use its serverRange as a default safe require
        serverMeta = self._capsMeta.get(family, {"serverRange": "*", "risk": "low"})
        serverReqRange: str = serverMeta["serverRange"]

        # Find existing grant
        grant = self.getGrant(principal, family)

        if not grant:
            raise GrantPermissionError(
                "PERMISSION_DENIED",
                f"Principal '{principal}' lacks permission grant for '{family}'",
                retryable=False,
                extra={"family": family, "required": serverReqRange, "has": None},
            )
        
        if grant.decision != "allow":
            raise GrantPermissionError(
                "PERMISSION_DENIED",
                f"Permission grant for '{family}' is denied",
                retryable=False,
                extra={"family": family, "required": serverReqRange, "has": grant.rangeExpr},
            )
        
        # If request specifies a version, enforce it against the grant's rangeExpr.
        # If request does NOT specify version, enforce against server baseline.
        if reqVer is not None:
            ok = versionInRange(reqVer, grant.rangeExpr)
            if not ok:
                raise GrantPermissionError(
                    "PERMISSION_DENIED",
                    f"Requested '{family}@{reqVer}' outside granted rangeExpr '{grant.rangeExpr}'",
                    retryable=False,
                    extra={"family": family, "requested": str(reqVer), "granted": grant.rangeExpr},
                )
        else:
            # No request version: ensure the grant at least covers the server's declared baseline
            # Example: server baseline is ^1; rangeExpr must include at least 1.0.0
            baseline = Version(serverReqRange.strip("^").split()[0] + ".0.0") if serverReqRange.startswith("^") and serverReqRange[1:].isdigit() else Version("0.0.0")
            if not versionInRange(baseline, grant.rangeExpr):
                raise GrantPermissionError(
                    "PERMISSION_BASELINE_UNSATISFIED",
                    f"Permission grant '{grant.rangeExpr}' does not satisfy server baseline '{serverReqRange}'",
                    retryable=False,
                    extra={"family": family, "baseline": serverReqRange, "granted": grant.rangeExpr},
                )
