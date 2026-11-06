# file: first-party/applications/experimentRunner/inputs.py ; version: 1
"""Launcher-only bounded authored definition and strict UTF-8 input snapshots."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import os
from pathlib import Path
import stat
import sys
import time

schema = importlib.import_module("first-party.applications.experimentRunner.definition")
pf = importlib.import_module("first-party.applications.experimentRunner.procedureFormat")


@dataclass(frozen=True, slots=True, repr=False)
class Source:
    """Own authored bytes and explicit source directory, not runtime authority."""

    raw: bytes
    normalized: bytes
    directory: Path
    format: str

    def value(self):
        """Return detached normalized meaning; raw spelling remains separately retained."""
        return schema.decode(self.normalized)


@dataclass(frozen=True, slots=True, repr=False)
class FrozenInputs:
    """Keep one immutable shared run snapshot, without reopening original files."""

    original: bytes
    format: str
    files: tuple

    def data(self):
        """Project logical IDs to immutable bytes shared by independent unit workspaces."""
        return {name: raw for name, path, raw, observation in self.files}


def preScan(raw):
    """Bound JSON5 nesting/tokens before a recursive parser allocates a JSON tree."""
    schema.require(type(raw) is bytes and len(raw) <= schema.MAX_BYTES, "Definition exceeds bound.")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise schema.ValidationError("Invalid authored UTF-8.") from None
    pf.text(value, schema.MAX_BYTES)
    quote, comment, escaped, depth, tokens, index = None, None, False, 0, 0, 0
    while index < len(value):
        char = value[index]
        following = value[index:index + 2]
        if comment == "line":
            if char in "\r\n":
                comment = None
        elif comment == "block":
            if following == "*/":
                comment, index = None, index + 1
        elif quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif following in ("//", "/*"):
            comment, index = ("line" if following == "//" else "block"), index + 1
        elif char in ("'", '"'):
            quote = char
            tokens += 1
        elif char in "[{":
            depth += 1
            tokens += 1
        elif char in "]}":
            depth -= 1
            tokens += 1
        elif not char.isspace():
            tokens += 1
        schema.require(0 <= depth <= 32 and tokens <= 65536, "Authored structure exceeds bound.")
        index += 1
    schema.require(depth == 0 and quote is None and comment != "block", "Unterminated authored input.")
    return value


def parse(raw, format):
    """Decode finite JSON5 or strict JSON, rejecting duplicates and trailing material."""
    value = preScan(raw)
    schema.require(format in ("json", "json5"), "Unsupported authoring format.")
    if format == "json":
        return schema.decode(raw)
    try:
        import json5
        result = json5.loads(value, allow_duplicate_keys=False, consume_trailing=True,
            parse_constant=schema.rejectConstant, parse_float=floating, parse_int=integer)
        pf.tree(result)
        schema.require(pf.isProcedure(result), "JSON5 requires definition3.")
        return result
    except (ValueError, TypeError, RecursionError, OverflowError, UnicodeError):
        raise schema.ValidationError("Invalid bounded JSON5.") from None


def integer(value, base=10):
    """Reject oversized authored numeric tokens before arbitrary-precision conversion."""
    schema.require(len(value) <= 128, "Numeric token exceeds bound.")
    return int(value, base)


def floating(value):
    """Bound floating-point token spelling before conversion, then reject overflow."""
    schema.require(len(value) <= 128, "Numeric token exceeds bound.")
    return schema.finiteFloat(value)


def readDefinition(path, format="json"):
    """Read exact authored bytes once, before any active runtime/tool admission."""
    path = Path(path).absolute()
    with path.open("rb") as source:
        raw = source.read(schema.MAX_BYTES + 1)
    value = parse(raw, format)
    return Source(raw, schema.canonical(value), path.parent, format)


def freeze(source, definition, policy):
    """Read every declared input once beneath explicit definition root with byte caps."""
    value = definition.value()
    schema.require(type(source) is Source, "Procedure needs an authored source snapshot.")
    root = source.directory.absolute()
    rootInfo = root.lstat()
    schema.require(not stat.S_ISLNK(rootInfo.st_mode) and not getattr(rootInfo, "st_file_attributes", 0) & 1024,
                   "Input root cannot be a link.")
    root = root.resolve(strict=True)
    files, total = [], 0
    for name, relative in sorted(value["inputs"].items()):
        path = root
        for part in (None, *relative.split("/")):
            if part is not None:
                path = path / part
            info = path.lstat()
            schema.require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 1024,
                           "Input source cannot be a link.")
        schema.require(stat.S_ISREG(info.st_mode) and path.resolve().is_relative_to(root), "Invalid input source.")
        with path.open("rb") as reader:
            before = os.fstat(reader.fileno())
            raw = reader.read(policy["procedureLimits"]["inputBytes"] + 1)
            after = os.fstat(reader.fileno())
        current = path.lstat()
        schema.require(stat.S_ISREG(current.st_mode) and not getattr(current, "st_file_attributes", 0) & 1024
            and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            and (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns), "Input source changed.")
        try:
            pf.text(raw.decode("utf-8", errors="strict"), policy["procedureLimits"]["inputBytes"])
        except UnicodeError:
            raise schema.ValidationError("Invalid input UTF-8.") from None
        total += len(raw)
        schema.require(total <= policy["procedureLimits"]["totalInputBytes"], "Input budget exceeded.")
        observation = (time.time_ns(), before.st_dev, before.st_ino, len(raw), before.st_mtime_ns,
                       before.st_ctime_ns, hashlib.sha256(raw).hexdigest())
        files.append((name, relative, raw, observation))
    return FrozenInputs(source.raw, source.format, tuple(files))


def parserIdentity(format):
    """Identify the actual authoring parser without requiring it for offline reading."""
    if format == "json5":
        from importlib.metadata import version
        return dict(name="json5", version=version("json5"))
    return dict(name="stdlib-json", version=sys.version.split()[0])


def tools(config):
    """Observe configured executables before activation without executing version probes."""
    result = []
    for name in sorted({c["tool"] for c in config["approvedCommands"].values()}):
        configured = config["processTools"][name]
        path = Path(configured).resolve(strict=True)
        schema.require(path.is_file(), "Configured tool is unavailable.")
        digest = None
        if config["archive"]["hashAssets"]:
            hasher = hashlib.sha256()
            with path.open("rb") as source:
                before = os.fstat(source.fileno())
                while block := source.read(1048576):
                    hasher.update(block)
                after = os.fstat(source.fileno())
            schema.require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "Configured tool changed.")
            digest = hasher.hexdigest()
        result.append(dict(toolName=name, configuredExecutable=configured, observedExecutable=str(path),
                           sha256=digest, version=None, availability="observed"))
    return result
