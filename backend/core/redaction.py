# backend/core/redaction.py
from __future__ import annotations

import re

__all__ = ["redactText"]



# Precompiled sensitive-data regex patterns
# Add more as needed (JWTs, tokens, credentials, etc.)
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bearer or Authorization headers
    (re.compile(r"(?iu)(Bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
    (re.compile(r"(?iu)(Authorization\s*[:=]\s*)[A-Za-z0-9._\-]+"), "\1***"),
    
    # Password-like fields in JSON or logs
    (re.compile(r'(?iu)("password"\s*:\s*")[^"]+(")'), r"\1***\2"),
    (re.compile(r'(?iu)("pass"\s*:\s*")[^"]+(")'), r'\1***\2'),

    # API key or token-style key/value pairs
    (re.compile(r'(?iu)("api[_\-]?key"\s*:\s*")[^"]+(")'), r'\1***\2'),
    (re.compile(r'(?iu)("token"\s*:\s*")[^"]+(")'), r'\1***\2'),
    (re.compile(r'(?iu)("viewToken"\s*:\s*")[^"]+(")'), r'\1***\2'),

    # Query parameter forms like token=abcdef
    (re.compile(r'(?iu)(token=)[^&\s]+'), r'\1***'),
    (re.compile(r'(?iu)(viewToken=)[^&\s]+'), r'\1***'),
]



def redactText(text: str) -> str:
    """Return sanitized text with sensitive substrings replaced by ***."""
    if not text:
        return text
    out = text
    for pattern, repl in _SENSITIVE_PATTERNS:
        try:
            out = pattern.sub(repl, out)
        except re.error:
            continue # Never crash logging on regex errors
    return out
