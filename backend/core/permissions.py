# backend/core/permissions.py
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from semantic_version import Version, NpmSpec

import logging
logger = logging.getLogger(__name__)

__all__ = [
    "Decision",
    "GrantPermission",
    "GrantPermissionError",
    "versionInRange",
    "parseCapability",
    "parseCapabilityRange",
    "PermissionManager",
]



Decision = Literal["allow", "deny"]



# ------------------------------------------------
#               Grants / error types
# ------------------------------------------------

@dataclass(frozen=True)
class GrantPermission:
    """
    A permission grant for a (principal, capability family), example: ("llama.cpp", "chat").
    The range is an NpmSpec range (e.g., "^1", ">=1.5 <2", "*").
    """
    principal: str                      # e.g. modId, "system", "devtools"
    family: str                         # capability family, e.g. "http.client", "chat"
    rangeSpec: NpmSpec                  # npm style semver range; e.g. "^1", "~1.2", ">=1.5 <2", "*"
    decision: Decision = "deny"
    scope: dict[str, Any] | None = None # Optional: { "hosts": ["api.example.com"] }
    expiresAtMs: int | None = None      # None => no expiry



class GrantPermissionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, extra: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.extra = extra or {}



# ------------------------------------------------
#                    Utilities
# ------------------------------------------------

def _nowMs() -> int:
    import time as _t
    return int(_t.time() * 1000)



def _isExpired(grantPerm: GrantPermission) -> bool:
    return grantPerm.expiresAtMs is not None and _nowMs() > grantPerm.expiresAtMs



def versionInRange(version: Version, specRange: NpmSpec) -> bool:
    """Returns True if the Version satisfies the given NpmSpec range."""
    return specRange.match(version)



def parseCapability(capStr: str) -> tuple[str, Version | None]:
    """
    Parses "family@version" where 'version' must be a valid npm semver version.
    Returns (family, Version) or raises ValueError if the capability string value is invalid.

    Examples:
      "chat@1.5"       -> ("chat", Version("1.5.0"))
      "chat@1.2.3"     -> ("chat", Version("1.2.3"))
      "chat"           -> raises ValueError
      "chat@^1"        -> raises ValueError
      "chat@v1.5"      -> raises ValueError
      "chat@banana"    -> raises ValueError
      "chat@banana-2"  -> raises ValueError
    
    If version part is malformed, returns (family, None).
    """
    capStr = (capStr or "").strip()
    if "@" not in capStr:
        family, verStr = capStr, ""
    else:
        family, verStr = capStr.split("@", 1)
    verStr = verStr.strip()

    return family, Version.coerce(verStr) # will raise ValueError if invalid



def parseCapabilityRange(capStr: str) -> tuple[str, NpmSpec]:
    """
    Parses "family@npm-range" where 'npm-range' must be a valid npm semver range.
    Returns (family, NpmSpec) or raises ValueError if npm-range is invalid or missing.

    Examples:
      "chat@^1"       -> ("chat", NpmSpec("^1"))
      "chat@~1.5"     -> ("chat", NpmSpec("~1.5"))
      "chat@>=1.5 <2" -> ("chat", NpmSpec(">=1.5 <2"))
      "chat@1.5"      -> ("chat", NpmSpec("1.5"))
      "chat"          -> ("chat", NpmSpec(""))
      "chat@banana"   -> raises ValueError
    """
    capStr = (capStr or "").strip()
    if "@" not in capStr:
        family, npmRange = capStr, ""
    else:
        family, npmRange = capStr.split("@", 1)
    return family, NpmSpec(npmRange) # will raise if verStr is invalid



# ------------------------------------------------
#                    Utilities
# ------------------------------------------------

class PermissionManager:
    """
    Minimal permission manager.

    - Stores capability baseline as NpmSpec per family.
    - Stores grants as NpmSpec ranges per (principal, family).
    - When ensuring a capability:
        • If a specific version is requested (family@version), version must match grant's range.
        • If no version is requested (just 'family'), we only require that a grant exists with
            decision == "allow" (no range match).
    
    NOTE: We will implement range-vs-range later, as semantic_version doesn't implement it and other libraries
          are not matching requirements or are orphaned. Use a specific version when requesting a grant.
    """
    def __init__(self):
        # (principal, family) -> GrantPermission
        self._grants: dict[tuple[str, str], GrantPermission] = {}
        # family -> {"serverSpec": NpmSpec, "risk": "low"|"medium"|"high" }
        self._capsMeta: dict[str, dict[str, Any]] = {}
    
    def registerCapability(self, *, capability: str, serverVersion: str, risk: str = "low") -> None:
        """
        Register a capability family and the baseline as an npm range, e.g.:
          "chat@^1", "http.client@>=1.5 <2", "gm.world@*"
        If no '@' is present, baseline becomes '' empty (any version).
        Raises ValueException on invalid npm range.
        """
        family, serverSpec = parseCapabilityRange(capability)
        self._capsMeta[family] = {
            "serverSpec": serverSpec,
            "risk": risk
        }
        
    # ---------- Grant management ----------
    def putGrant(self, grant: GrantPermission) -> None:
        self._grants[(grant.principal, grant.family)] = grant
    
    def revokeGrant(self, principal: str, family: str) -> None:
        self._grants.pop((principal, family), None)
    
    def getGrant(self, principal: str, family: str) -> GrantPermission | None:
        grant = self._grants.get((principal, family))
        if grant and _isExpired(grant):
            self._grants.pop((principal, family), None)
            return None
        return grant
    
    # ---------- Enforcement ----------
    def ensure(self, *, principal: str, capability: str) -> None:
        """
        Enforce permission for a requested capability.
        'capability' can be:
          - "family@version"       - version is coerced, or raises ValueError if invalid.
          - "family" (no version)  - we only check that an "allow" grant exists.
        
        NOTE: semantic_version doesn't impement npm ranges intersection and other libraries are
              not matching requirements or are orphaned. Use a specific version when requesting a grant.
        
        Raises GrantPermissionError if not allowed.
        """
        family, reqVer = parseCapability(capability) # may raise ValueError
        
        # Find existing grant
        grant = self.getGrant(principal, family)
        if not grant:
            raise GrantPermissionError(
                "PERMISSION_DENIED",
                f"Principal '{principal}' lacks permission grant for '{family}'",
                retryable=False,
                extra={"family": family, "decission": "denied"},
            )
        
        if grant.decision != "allow":
            raise GrantPermissionError(
                "PERMISSION_DENIED",
                f"Permission grant for '{family}' is denied",
                retryable=False,
                extra={"family": family, "grant_range": str(grant.rangeSpec), "decision": grant.decision},
            )
        
        if reqVer is not None:
            # Caller asked for a concrete version -> check against the granted range.
            ok = versionInRange(reqVer, grant.rangeSpec)
            if not ok:
                raise GrantPermissionError(
                    "PERMISSION_DENIED",
                    f"Requested '{family}@{reqVer}' outside granted rangeExpr '{grant.rangeSpec}'",
                    retryable=False,
                    extra={"family": family, "requested": str(reqVer), "grant_range": str(grant.rangeSpec), "decision": grant.decision},
                )
        
        return # No further checks, you can proceed.
