# file: backend/tracing/devTrace.py
from __future__ import annotations

from datetime import UTC, datetime
from sys import stdout
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


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
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        attrText = formatAttrs(attrs)

        if attrText:
            print(f"[dev {timestamp}] {reason}: {message} {attrText}", file=stdout)
        else:
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
