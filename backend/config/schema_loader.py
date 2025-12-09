# backend/config/schema_loader.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

import json5

from backend.core.schema_registry import SchemaDoc, Descriptor, SchemaRegistry, JSONSchemaRoot, SEMVER_PATTERN_RE

logger = logging.getLogger(__name__)

__all__ = ["loadConfigSchemas"]

_PREFIX = "config."
_SUFFIXES = (".schema.json", ".schema.json5")



def loadConfigSchemas(registry: SchemaRegistry, schemaDir: Path) -> int:
    """
    Loads all config-related JSON Schemas from `schemaDir` into `registry`.

    Filenames must follow one of patterns:
      • config.<name>.schema.json
      • config.<name>.schema.json5
      • config.<name>@<version>.schema.json
      • config.<name>@<version>.schema.json5
    
    Examples:
      config.global.schema.json
      config.realm@1.2.3.schema.json
      config.graphics@2.0.0-alpha.1.schema.json5

    Returns the number of successfully registered schemas.
    """
    if not schemaDir.exists():
        logger.warning("Schema directory does not exist: '%s'", schemaDir)
        return 0
    
    count = 0
    for file in schemaDir.iterdir():
        # Skip directories and dotfiles/temporary files
        if not file.is_file():
            continue
        if file.name.startswith(".") or file.name.endswith("~"):
            continue
        if not file.name.startswith(_PREFIX):
            continue
        if not _hasSchemaSuffix(file.name):
            continue

        try:
            name, version = _parseNameAndVersion(file.name)
            data = _readJsonLike(file)
            _ensureSchemaRootType(file, data)

            desc = Descriptor(namespace="config", name=name, version=version)
            doc = SchemaDoc(desc=desc, schema=cast(JSONSchemaRoot, data))
            registry.addSchema(doc)
            count += 1

            logger.debug("Loaded schema %s:%s@%s from '%s'", desc.namespace, name, version, file.name)
        except Exception as err:
            logger.error("Failed to load schema from '%s': %s", file, err)

    return count



def _hasSchemaSuffix(filename: str) -> bool:
    return filename.endswith(_SUFFIXES)



def _matchSuffix(rest: str) -> str | None:
    for sfx in _SUFFIXES:
        if rest.endswith(sfx):
            return sfx
    return None



def _parseNameAndVersion(filename: str) -> tuple[str, str]:
    """
    Parse '<prefix><rest><suffix>' where:
      prefix = _PREFIX
      suffix = one of _SUFFIXES
    
    From <rest>:
      - If it contains '@', split at the last '@' → left=name, right=version
      - Else name=<rest>, version='1.0.0'
    
    Validates version with SEMVER_PATTERN_RE.
    """
    # Strip prefix
    if not filename.startswith(_PREFIX):
        raise ValueError(f"Filename must start with '{_PREFIX}': '{filename}'")
    rest = filename[len(_PREFIX) :]

    # Strip suffix
    _suffix = _matchSuffix(rest)
    if _suffix is None:
        raise ValueError(f"Filename must end with one of {_SUFFIXES}: '{filename}'")
    rest = rest[: -len(_suffix)]

    # Split at the last '@'
    if "@" in rest:
        name, version = rest.rsplit("@", 1)
        if not name:
            raise ValueError(f"Missing name before '@' in '{filename}'")
        if not version:
            raise ValueError(f"Missing version after '@' in '{filename}'")
    else:
        name, version = rest, "1.0.0"
    
    if not SEMVER_PATTERN_RE.match(version):
        raise ValueError(
            f"Invalid semver '{version}' in filename '{filename}'. "
             " Expected MAJOR.MINOR.PATCH with optional pre-release/build."
        )
    
    return name, version



def _readJsonLike(file: Path) -> Any:
    """
    Read a JSON or JSON5 file and return parsed data.
    """
    text = file.read_text(encoding="utf-8")
    try:
        if file.name.endswith(".json5"):
            return json5.loads(text)
        return json.loads(text)
    except Exception as err:
        raise ValueError(f"Parse error in '{file}': {err}") from err



def _ensureSchemaRootType(file: Path, data: Any) -> None:
    """
    Ensure the top-level schema is valid (object or boolean schema).
    """
    if isinstance(data, (dict, bool)):
        return
    raise TypeError(
        f"Top-level of '{file.name}' must be a JSON object or boolean schema, not '{type(data).__name__}'"
    )
