# file: backend/tracing/devTrace.py
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from sys import stdout


class DevTraceSink:
    """
    Disposable development diagnostics sink.

    This is not DA-03 trace.

    It is not retained causal proof, not replay evidence, not audit evidence,
    not debugger evidence, and not committed-state authority.
    """

    def emit(
        self,
        reason: str,
        message: str,
        attrs: Mapping[str, object] | None = None,
    ) -> None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        attrText = formatAttrs(attrs)

        if attrText:
            print(f"[dev {timestamp}] {reason}: {message} {attrText}", file=stdout)

        print(f"[dev {timestamp}] {reason}: {message}", file=stdout)


def formatAttrs(attrs: Mapping[str, object] | None) -> str:
    if attrs is None:
        return ""

    parts: list[str] = []

    for key, value in attrs.items():
        parts.append(f"{key}={formatAttrValue(value)}")

    return " ".join(parts)


def formatAttrValue(value: object) -> str:
    text = str(value)

    if not text:
        return '""'

    if any(char.isspace() for char in text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    return text
