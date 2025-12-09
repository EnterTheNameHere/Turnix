# backend/core/schema_registry.py
from __future__ import annotations
import re
import copy
import threading
from dataclasses import dataclass, field
from typing import Any, TypeAlias, Callable, cast
from collections.abc import Mapping, Sequence

import fastjsonschema

from backend.core.utils import deepEquals
from backend.semver.semver import SEMVER_PATTERN_RE

__all__ = [
    "Descriptor",
    "SchemaDoc",
    "SchemaRegistry",
    "ValidationError",
    "JSONValue",
    "JSONSchemaRoot",
    "ValidatorFn",
]



JSONValue: TypeAlias = (None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"])

# JSON Schema roots can be a dict or a top-level boolean schema:
JSONSchemaRoot: TypeAlias = dict[str, JSONValue] | bool

# Type alias for compiled validators
ValidatorFn: TypeAlias = Callable[[Any], None]



@dataclass(frozen=True)
class Descriptor:
    """
    Identifies a schema logically (namespace+name) and by version.
    - namespace: "global" | "modpack" | "game" | "mod" | "asset" | "runtime" | "rpc"
    - name: free-form id; for RPC use "route@version" (e.g., "chat.thread@1")
    - version: SemVer string; stable releases outrank pre-releases of same MAJOR.MINOR.PATCH
    - priority: tiebreaker within namespace/name when semver keys are equal (higher wins)
    """
    namespace: str
    name: str
    version: str
    priority: int = 0

    def key(self) -> tuple[str, int, tuple[int, int, int, int], str]:
        # Sorting key used for choosing the "max" version per (namespace, name)
        return (self.namespace, self.priority, self._semverKey(), self.name)
    
    def _semverKey(self) -> tuple[int, int, int, int]:
        """
        Returns a tuple comparable with normal tuple ordering:
            (major, minor, patch, stabilityFlag)
        Where stabilityFlag: 1 for stable (no prerelease), 0 for prerelease.
        This ensures 1.2.3 > 1.2.3-alpha when selecting 'max'.
        """
        mtch = SEMVER_PATTERN_RE.match(self.version or "")
        if not mtch:
            return (0, 0, 0, 0)
        major = int(mtch.group("major"))
        minor = int(mtch.group("minor"))
        patch = int(mtch.group("patch"))
        prerelease = mtch.group("prerelease")
        isStable = 0 if prerelease else 1
        return (major, minor, patch, isStable)



@dataclass
class SchemaDoc:
    """
    One JSON Schema document (Draft 2020-12), plus external references.
    - schema: the primary JSON Schema; if it has an "$id", it will be indexed
    - refs: mapping of external "$id" -> schema definitions to resolve absolute $ref
    """
    desc: Descriptor
    schema: JSONSchemaRoot
    refs: dict[str, JSONSchemaRoot] = field(default_factory=dict)



class ValidationError(Exception):
    pass



class SchemaRegistry:
    """
    Loads JSON Schemas, resolves $ref across documents (absolute ids, absolute+fragment/anchor,
    and local fragments "#/..."), compiles validators, and validates instances.
    Thread safe for concurrent readers/writers via internal RLock.
    """
    def __init__(self):
        # Highest version (by Descriptor.key) kept per (namespace, name)
        self._docs: dict[tuple[str, str], SchemaDoc] = {}
        # Global "$id" index for absolute $ref resolution
        self._byId: dict[str, JSONSchemaRoot] = {}
        # Global "id#anchor" → node
        self._anchors: dict[str, Any] = {}
        # Compiled validators cache per (namespace, name)
        self._validators: dict[tuple[str, str], ValidatorFn] = {}
        # Resolved-schema cache per (namespace, name)
        self._resolvedCache: dict[tuple[str, str], JSONSchemaRoot] = {}
        # Coarse lock
        self._lock = threading.RLock()
    
    # ----- Internal: id/anchor indexing -----

    def _deepEqual(self, first: Any, second: Any) -> bool:
        return deepEquals(first, second, strict=False)
        
    def _invalidateAllCaches(self) -> None:
        self._validators.clear()
        self._resolvedCache.clear()

    # ----- Registration -----
    
    def addSchema(self, doc: SchemaDoc) -> None:
        with self._lock:
            key = (doc.desc.namespace, doc.desc.name)
            existing = self._docs.get(key)
            if existing and existing.desc.key() >= doc.desc.key():
                # Keep existing if it sorts higher or equal by our key
                return
            
            # Compute "allowed to overwrite" ids/anchors from the doc being superseded
            allowedIds: set[str] = set()
            allowedAnchors: set[str] = set()

            def collectIdsAnchors(node: Any, *, baseId: str | None):
                if isinstance(node, Mapping):
                    if "$id" in node and isinstance(node["$id"], str):
                        base = node["$id"]
                        allowedIds.add(base)
                        baseId = base
                    if "$anchor" in node and isinstance(node["$anchor"], str) and baseId:
                        allowedAnchors.add(f"{baseId}#{node['$anchor']}")
                    for value in node.values():
                        if isinstance(value, (Mapping, list)):
                            collectIdsAnchors(value, baseId=baseId)
                elif isinstance(node, list):
                    for value in node:
                        collectIdsAnchors(value, baseId=baseId)
            
            if existing:
                # External refs on the old doc
                for refId, ref in (existing.refs or {}).items():
                    if isinstance(refId, str):
                        allowedIds.add(refId) # The ref root itself
                        if isinstance(ref, Mapping):
                            collectIdsAnchors(ref, baseId=refId)
                
                # Old root + nested
                oldRootId = existing.schema.get("$id") if isinstance(existing.schema, Mapping) else None
                if isinstance(oldRootId, str):
                    allowedIds.add(oldRootId)
                collectIdsAnchors(existing.schema, baseId=oldRootId if isinstance(oldRootId, str) else None)

            # Stage 1: build a temporary index to detect collisions before mutating global index
            stagedIds: dict[str, JSONSchemaRoot] = {}
            stagedAnchors: dict[str, Any] = {}

            def stageIndexId(schemaId: str, node: JSONSchemaRoot):
                ex = self._byId.get(schemaId)
                if ex is not None and not self._deepEqual(ex, node) and schemaId not in allowedIds:
                    raise ValueError(f"Schema $id collision for '{schemaId}': different content already registered")
                ex2 = stagedIds.get(schemaId)
                if ex2 is not None and not self._deepEqual(ex2, node):
                    raise ValueError(f"Schema $id collision (staged) for '{schemaId}'")
                stagedIds[schemaId] = copy.deepcopy(node) if isinstance(node, dict) else node
            
            def stageIndexAnchor(baseIdWithHash: str, node: Any):
                ex = self._anchors.get(baseIdWithHash)
                # Allow overwrite if this anchor came from the doc we're superseding
                if ex is not None and not self._deepEqual(ex, node) and baseIdWithHash not in allowedAnchors:
                    raise ValueError(
                        f"Schema $anchor collision for '{baseIdWithHash}': different content already registered"
                    )
                ex2 = stagedAnchors.get(baseIdWithHash)
                if ex2 is not None:
                    raise ValueError(f"Schema $anchor collision (staged) for '{baseIdWithHash}'")
                stagedAnchors[baseIdWithHash] = copy.deepcopy(node) if isinstance(node, (dict, list)) else node
            
            def stagedWalkRef(node: JSONValue, *, baseId: str | None):
                # Recurse into arrays
                if isinstance(node, list):
                    for item in node:
                        stagedWalkRef(item, baseId=baseId)
                    return
                
                # Recurse into JSON objects (schema objects)
                if isinstance(node, Mapping):
                    if "$id" in node and isinstance(node["$id"], str):
                        baseId = node["$id"]
                        stageIndexId(baseId, node) # Accepts object-schema only (Mapping)

                    if "$anchor" in node and isinstance(node["$anchor"], str) and baseId:
                        stageIndexAnchor(f"{baseId}#{node['$anchor']}", node)
                    
                    for value in node.values():
                        if isinstance(value, (Mapping, list)):
                            stagedWalkRef(value, baseId=baseId)
                    return
                
                # Scalars (str/int/float/bool/None) - nothing to do
                return

            # Stage external refs (index root id, nested $id, and $anchor under base=refId)
            for refId, ref in (doc.refs or {}).items():
                if isinstance(refId, str):
                    # bool refs get indexed, but not walked (as there's nothing to walk)
                    stageIndexId(refId, ref)

                    if isinstance(ref, Mapping):
                        stagedWalkRef(ref, baseId=refId)

            # Stage root id + walk for nested ids/anchors
            rootId = doc.schema.get("$id") if isinstance(doc.schema, Mapping) else None
            if isinstance(rootId, str) and isinstance(doc.schema, Mapping):
                stageIndexId(rootId, doc.schema)


            def stagedWalk(node: JSONValue, *, baseId: str | None) -> None:
                # Recurse into arrays
                if isinstance(node, list):
                    for item in node:
                        stagedWalk(item, baseId=baseId)
                    return
                
                # Recurse into JSON objects (schema objects)
                if isinstance(node, Mapping):
                    if "$id" in node and isinstance(node["$id"], str):
                        baseId = node["$id"]
                        stageIndexId(baseId, node) # Accepts object-schema only (Mapping)

                    if "$anchor" in node and isinstance(node["$anchor"], str) and baseId:
                        stageIndexAnchor(f"{baseId}#{node['$anchor']}", node)

                    for value in node.values():
                        if isinstance(value, (Mapping, list)):
                            stagedWalk(value, baseId=baseId)
                    return
                
                # Scalars (str/int/float/bool/None) - nothing to do
                return
            

            stagedWalk(doc.schema, baseId=rootId if isinstance(rootId, str) else None)

            # Stage succeeded → commit changes
            self._docs[key] = doc
            # Merge staged ids and anchors into the global index
            self._byId.update(stagedIds)
            self._anchors.update(stagedAnchors)

            # Invalidate caches globally due to id/anchor index change
            self._invalidateAllCaches()
    
    def addSchemas(self, docs: list[SchemaDoc]) -> None:
        for doc in docs:
            self.addSchema(doc)
    
    def removeSchema(self, namespace: str, name: str, *, purgeIds: bool = False) -> bool:
        """
        Remove the highest-version doc for (namespace, name). If purgeIds=True,
        attempt to drop its $id/anchors and ref $id's from the indexes.
        NOTE: This may break other schemas that depend on these ids/anchors.
        """
        with self._lock:
            key = (namespace, name)
            doc = self._docs.pop(key, None)
            if not doc:
                return False

            self._validators.pop(key, None)
            self._resolvedCache.pop(key, None)
            
            if purgeIds:
                # Remove external refs
                for refId, refNode in (doc.refs or {}).items():
                    if isinstance(refId, str):
                        # Keep if any other doc contributes the same ref content
                        keep = False
                        for(ns2, name2), other in self._docs.items():
                            if(ns2, name2) == (namespace, name):
                                continue
                            otherRef = (other.refs or {}).get(refId)
                            if otherRef is not None and self._deepEqual(otherRef, refNode):
                                keep = True
                                break
                        
                        if not keep:
                            self._byId.pop(refId, None)
                            # Also remove any anchors under this id (best-effort heuristic)
                            toDelete = [aid for aid in self._anchors.keys() if aid.startswith(refId + "#")]
                            for aid in toDelete:
                                self._anchors.pop(aid, None)
                # Remove root id and nested ids/anchors
                rootId = doc.schema.get("$id") if isinstance(doc.schema, Mapping) else None
                # Collect ids/anchors we previously indexed from this doc
                collectedIds: set[str] = set()
                collectedAnchors: set[str] = set()

                def collect(node: Any, *, baseId: str | None):
                    if not isinstance(node, Mapping):
                        if isinstance(node, list):
                            for item in node:
                                collect(item, baseId=baseId)
                        return
                    if "$id" in node and isinstance(node["$id"], str):
                        baseId = node["$id"]
                        collectedIds.add(baseId)
                    if "$anchor" in node and isinstance(node["$anchor"], str) and baseId:
                        collectedAnchors.add(f"{baseId}#{node['$anchor']}")
                    for value in node.values():
                        if isinstance(value, Mapping) or isinstance(value, list):
                            collect(value, baseId=baseId)
                
                collect(doc.schema, baseId=rootId if isinstance(rootId, str) else None)

                for sid in collectedIds:
                    self._byId.pop(sid, None)
                for aid in collectedAnchors:
                    self._anchors.pop(aid, None)
            
            # Global invalidation because id/anchor index may have changed
            self._invalidateAllCaches()
            return True
    
    def hasSchema(self, namespace: str, name: str) -> bool:
        with self._lock:
            return (namespace, name) in self._docs

    def getSchema(self, namespace: str, name: str) -> JSONSchemaRoot | None:
        with self._lock:
            doc = self._docs.get((namespace, name))
            return copy.deepcopy(doc.schema) if doc else None
    
    def listSchema(self, *, namespace: str | None = None) -> list[tuple[str, str, str]]:
        """
        Returns a stable list of (namespace, name, version) for all registered highest-version docs.
        """
        with self._lock:
            items: list[tuple[str, str, str]] = []
            for(ns, name), doc in self._docs.items():
                if namespace and ns != namespace:
                    continue
                items.append((ns, name, doc.desc.version))
            items.sort(key=lambda item: (item[0], item[1])) # Stable sort
            return items

    def getById(self, schemaId: str) -> JSONSchemaRoot | None:
        with self._lock:
            node = self._byId.get(schemaId)
            if node is None:
                return None
            return copy.deepcopy(node) if isinstance(node, dict) else node # Returns for bool too

    def clear(self) -> None:
        with self._lock:
            self._docs.clear()
            self._validators.clear()
            self._resolvedCache.clear()
            self._byId.clear()
            self._anchors.clear()
    
    # ----- JSON Pointer utilities -----
    
    def _jsonPointer(self, root: Any, fragment: str) -> Any:
        """
        Resolve a JSON Pointer fragment (beginning with '#', e.g. '#/a/b/0').
        Supports dict and list navigation. Returns None if path can't be resolved.
        """
        if not isinstance(fragment, str):
            return None
        if not fragment.startswith("#"):
            return None
        path = fragment[1:] # Drop '#'
        if path == "":
            return root
        if not path.startswith("/"):
            return None
        current = root
        for token in path.split("/")[1:]:
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, Mapping):
                if token in current:
                    current = current[token]
                else:
                    return None
            elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
                # Try to interpret token as index
                try:
                    idx = int(token)
                except Exception:
                    return None
                if idx < 0 or idx >= len(current):
                    return None
                current = current[idx]
            else:
                return None
        return current
    
    # ----- Compilation & $ref resolution -----

    def _resolveRefs(self, schema: JSONSchemaRoot, *, cacheKey: tuple[str, str]) -> JSONSchemaRoot:
        """
        Resolves:
          • Absolute $ref: "id://Foo"
          • Absolute + fragment: "id://Foo#/$defs/Bar"
          • Local fragment: "#/..."
        Leaves unknown $ref as-is (validator will surface issues).
        Uses a per-(namespace, name) memoization layer.
        """
        with self._lock:
            cached = self._resolvedCache.get(cacheKey)
        
        if cached is not None:
            return cached
        
        if not isinstance(schema, Mapping):
            # boolean schema; nothing to resolve, but cache it too
            with self._lock:
                self._resolvedCache[cacheKey] = schema
            return schema

        root = copy.deepcopy(schema) # Never mutate the caller's tree

        # Per-root memo for local anchors (name -> node)
        localAnchorCache: dict[str, Any] = {}

        def findLocalAnchor(rootDoc: Any, name: str) -> Any | None:
            if name in localAnchorCache:
                return localAnchorCache[name]
            
            def walk(node: Any):
                if isinstance(node, Mapping):
                    anchor = node.get("$anchor")
                    if isinstance(anchor, str) and anchor == name:
                        return node
                    for value in node.values():
                        found = walk(value)
                        if found is not None:
                            return found
                elif isinstance(node, list):
                    for value in node:
                        found = walk(value)
                        if found is not None:
                            return found
                return None
            found = walk(rootDoc)
            if found is not None:
                localAnchorCache[name] = found
            return found

        def resolve(node: Any, *, seen: set[str] | None = None, rootDoc: Any = None) -> Any:
            if seen is None:
                seen = set()
            if rootDoc is None:
                rootDoc = root
            
            if isinstance(node, Mapping):
                refId = node.get("$ref")
                if isinstance(refId, str):
                    # Local reference to the document root ("#"):
                    # Resolve to empty schema {} to break cycles (self-ref is a no-op).
                    # This avoids infinite recursion during validation.
                    if refId == "#":
                        return {}
                    # Local JSON Pointer staring: "#/..."
                    if refId.startswith("#/"):
                        target = self._jsonPointer(rootDoc, refId)
                        return resolve(target, seen=seen, rootDoc=rootDoc) if target is not None else node

                    # Local anchor: "#AnchorName"
                    if refId.startswith("#") and len(refId) > 1:
                        # By now we know it's not "#/" as that's caught in the branch above
                        anchorName = refId[1:]
                        target = findLocalAnchor(rootDoc, anchorName)
                        return resolve(target, seen=seen, rootDoc=rootDoc) if target is not None else node

                    # Absolute id with optional fragment
                    base, frag = (refId.split("#", 1) + [""])[:2]

                    # Absolute self-ref to the current document root with no fragment is a no-op.
                    # Replace with {} to avoid infinite recursion in the backend validator.
                    try:
                        currentRootId = rootDoc.get("$id") if isinstance(rootDoc, Mapping) else None
                    except Exception:
                        currentRootId = None
                    if frag == "" and currentRootId and base == currentRootId:
                        return {}

                    # Full anchor ref (id#AnchorName) - check anchors first
                    if frag and not frag.startswith("/"):
                        fullAnchor = f"{base}#{frag}"
                        with self._lock:
                            if fullAnchor in self._anchors:
                                return resolve(self._anchors[fullAnchor], seen=seen, rootDoc=self._byId.get(base, root))

                    # Guard against circular reference on the full ref string
                    if refId in seen:
                        return node # Leave as-is; validator can catch cycles if problematic
                    seen.add(refId)

                    with self._lock:
                        baseDoc = self._byId.get(base)
                    if baseDoc is None:
                        return node # Unknown id; leave as-is
                    
                    # Boolean schemas can't have fragments
                    if isinstance(baseDoc, bool):
                        if frag: # pointer or anchor on a boolean → invalid; leave unresolved
                            return node
                        return resolve(baseDoc, seen=seen, rootDoc=rootDoc)
                    
                    # Object schema
                    target = baseDoc
                    if frag:
                        if frag.startswith("/"):
                            target = self._jsonPointer(baseDoc, "#" + frag)
                            if target is None:
                                return node # Bad pointer; leave as-is
                        else:
                            # Absolute anchor fallback should have been handled above; leave unresolved
                            return node
                    
                    # Recurse into the referenced schema to resolve nested refs too.
                    return resolve(target, seen=seen, rootDoc=baseDoc)
                
                # Regular object: deep-resolve children
                return {key: resolve(value, seen=seen, rootDoc=rootDoc) for key, value in node.items()}
            
            if isinstance(node, list):
                return [resolve(item, seen=seen, rootDoc=rootDoc) for item in node]
            
            return node
        
        resolved = resolve(root)
        with self._lock:
            self._resolvedCache[cacheKey] = resolved # Memoize the resolved tree
        return resolved

    def _compile(self, namespace: str, name: str) -> ValidatorFn:
        key = (namespace, name)

        with self._lock:
            existing = self._validators.get(key)
            if existing is not None:
                return existing
            doc = self._docs.get(key)
            if not doc:
                raise KeyError(f"Schema not found: {namespace}:{name}")
            
        resolved = self._resolveRefs(doc.schema, cacheKey=key)
        validator: ValidatorFn
        
        if isinstance(resolved, bool):
            # Boolean schema
            def boolValidator(instance: Any) -> None:
                if not resolved:
                    raise fastjsonschema.JsonSchemaValueException("Instance is not allowed by boolean schema false")
            validator = boolValidator
        else:
            # fastjsonschema.compile returns an untyped callable → cast it
            validator: ValidatorFn = cast(ValidatorFn, fastjsonschema.compile(resolved))
        
        with self._lock:
            self._validators[key] = validator
        return validator

    def compileAll(self) -> None:
        with self._lock:
            keys = list(self._docs.keys())
        for (namespace, name) in keys:
            self.getValidator(namespace, name)
    
    def getValidator(self, namespace: str, name: str) -> ValidatorFn:
        return self._compile(namespace, name)

    # ----- Validation & diagnostics -----

    def validate(self, *, namespace: str, name: str, instance: Any) -> None:
        try:
            validator = self.getValidator(namespace, name)
            validator(instance)
        except Exception as err:
            # More helpful message including schema identity
            raise ValidationError(f"{namespace}:{name} validation failed: {err}") from err

    def findUnresolvedRefs(self) -> list[str]:
        """
        Returns a sorted list of unresolved absolute $ref ids across all registered roots.
        (Local fragments "#/..." are ignored; we only report absolute ids not present in _byId.)
        """
        # Snapshot everything needed under the lock to avoid churn
        with self._lock:
            docs = list(self._docs.values())
            byIdKeys: set[str] = set(self._byId.keys())

        missing: set[str] = set()

        def walk(node: Any):
            if isinstance(node, Mapping):
                refId = node.get("$ref")
                if isinstance(refId, str) and refId and not refId.startswith("#"):
                    base = refId.split("#", 1)[0]
                    if base and base not in byIdKeys:
                        missing.add(base)
                    # Anchors are ignored; do nothing with them
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        
        for doc in docs:
            walk(doc.schema)

        return sorted(missing)
