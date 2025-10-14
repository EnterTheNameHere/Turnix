# backend/core/redaction.py
from __future__ import annotations
import re



def defaultRedactor(text: str) -> str:
    """Returns a sanitized copy of text with tokens and passwords redacted."""
    # Hide bearer tokens & simple passwords
    # TODO: Expand protection for API keys, JWTs, and other sensitive patterns
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", text)
    text = re.sub(r'(?i)("password"\s*:\s*")[^"]+?(")', r'\1***\2', text)
    return text
