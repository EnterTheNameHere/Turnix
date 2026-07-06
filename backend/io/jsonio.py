# file: backend/io/jsonio.py
from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any

from backend.core.errors import UsageError
from backend.core.ids import uuidv4hex

if TYPE_CHECKING:
    from pathlib import Path


JsonObject = dict[str, Any]


def loadJsonFile(path: Path) -> JsonObject:
    try:
        text = path.read_text(encoding="utf-8")

    except OSError as err:
        raise UsageError(f"Failed to read JSON file {path}: {err}.") from err
    except UnicodeDecodeError as err:
        raise UsageError(f"Failed to decode JSON file {path} as UTF-8: {err}") from err

    try:
        value = json.loads(text)

    except json.JSONDecodeError as err:
        raise UsageError(f"Invalid JSON in {path}: {err}") from err
    except Exception as err:
        raise UsageError(f"Failed to parse JSON file {path}: {err}") from err

    if not isinstance(value, dict):
        raise UsageError(f"JSON file {path} must contain an object at the top level.")

    return value


def writeJsonFile(
    path: Path,
    value: JsonObject,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = True,
) -> None:
    text = json.dumps(
        value,
        indent=indent,
        ensure_ascii=ensure_ascii,
        sort_keys=sort_keys,
    ) + "\n"

    writeTextFileAtomically(path=path, text=text)


def loadJson5File(path: Path) -> JsonObject:
    try:
        import json5  # noqa: PLC0415
    except Exception as err:
        raise UsageError("json5 package is required to load JSON5 files.") from err

    try:
        text = path.read_text(encoding="utf-8")

    except OSError as err:
        raise UsageError(f"Failed to read JSON5 file {path}: {err}.") from err
    except UnicodeDecodeError as err:
        raise UsageError(f"Failed to decode JSON5 file {path} as UTF-8: {err}") from err

    try:
        value = json5.loads(text)

    except Exception as err:
        raise UsageError(f"Failed to parse JSON5 file {path}: {err}.") from err

    if not isinstance(value, dict):
        raise UsageError(f"JSON5 file {path} must contain an object at the top level.")

    return value


def writeTextFileAtomically(*, path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmpPath = path.with_name(f".{path.name}.{uuidv4hex()}.tmp")

    try:
        tmpPath.write_text(text, encoding="utf-8", newline="\n")
        tmpPath.replace(path)

    except OSError as err:
        with contextlib.suppress(OSError):
            tmpPath.unlink()

        raise UsageError(f"Failed to write text file {path}: {err}") from err
