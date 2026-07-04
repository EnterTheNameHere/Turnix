# file: backend/core/json5Loader.py
from __future__ import annotations

from pathlib import Path

import json5

from backend.core.errors import UsageError


def loadJson5File(path: Path) -> dict[str, object]:
    try:
        return json5.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise UsageError(f"Failed to read JSON5 file {path}: {err}.") from err
    except Exception as err:
        raise UsageError(f"Failed to parse JSON5 file {path}: {err}.") from err
