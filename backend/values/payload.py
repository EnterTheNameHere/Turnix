# file: backend/values/payload.py ; version: 2
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import RLock
from typing import Final

__all__ = ["Chunk", "ChunkValueRef", "InlineValueRef", "InMemoryChunkStore", "ValueRef", "decodeJsonValue", "encodeJsonValue"]

_JSON_CODEC: Final[str] = "json.utf8.canonical@1"


@dataclass(frozen=True, slots=True)
class Chunk:
    """Immutable content-addressed payload bytes."""

    chunkId: str
    chunkType: str
    payload: bytes
    contentHash: str

    @classmethod
    def create(cls, *, chunkType: str, payload: bytes) -> "Chunk":
        if type(chunkType) is not str or not chunkType:
            raise ValueError("chunkType must be a non-empty string.")
        if type(payload) is not bytes:
            raise TypeError("payload must be exact bytes.")
        contentHash = hashlib.sha256(payload).hexdigest()
        identityMaterial = chunkType.encode("utf-8") + b"\0" + payload
        chunkId = f"sha256:{hashlib.sha256(identityMaterial).hexdigest()}"
        return cls(chunkId=chunkId, chunkType=chunkType, payload=payload, contentHash=contentHash)


@dataclass(frozen=True, slots=True)
class InlineValueRef:
    codecId: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ChunkValueRef:
    codecId: str
    chunkId: str
    contentHash: str


type ValueRef = InlineValueRef | ChunkValueRef


class InMemoryChunkStore:
    """Thread-safe in-memory realization of immutable Chunk storage."""

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._lock = RLock()

    def put(self, chunk: Chunk) -> None:
        if not isinstance(chunk, Chunk):
            raise TypeError("chunk must be a Chunk.")
        with self._lock:
            existing = self._chunks.get(chunk.chunkId)
            if existing is not None and existing != chunk:
                raise RuntimeError(f"Chunk identity collision: {chunk.chunkId}.")
            self._chunks[chunk.chunkId] = chunk

    def require(self, chunkId: str) -> Chunk:
        with self._lock:
            try:
                chunk = self._chunks[chunkId]
            except KeyError as err:
                raise LookupError(f"Chunk is not retained: {chunkId}.") from err
        if hashlib.sha256(chunk.payload).hexdigest() != chunk.contentHash:
            raise RuntimeError(f"Chunk integrity verification failed: {chunkId}.")
        expectedId = Chunk.create(chunkType=chunk.chunkType, payload=chunk.payload).chunkId
        if expectedId != chunk.chunkId:
            raise RuntimeError(f"Chunk identity verification failed: {chunkId}.")
        return chunk


def encodeJsonValue(value: object, *, store: InMemoryChunkStore, inlineLimitBytes: int = 1024) -> ValueRef:
    if type(inlineLimitBytes) is not int or inlineLimitBytes < 0:
        raise ValueError("inlineLimitBytes must be a non-negative exact integer.")
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as err:
        raise TypeError(f"Committed value is not deterministically JSON encodable: {err}.") from err
    if len(payload) <= inlineLimitBytes:
        return InlineValueRef(codecId=_JSON_CODEC, payload=payload)
    chunk = Chunk.create(chunkType="stateValue", payload=payload)
    store.put(chunk)
    return ChunkValueRef(codecId=_JSON_CODEC, chunkId=chunk.chunkId, contentHash=chunk.contentHash)


def decodeJsonValue(valueRef: ValueRef, *, store: InMemoryChunkStore) -> object:
    if not isinstance(valueRef, (InlineValueRef, ChunkValueRef)):
        raise TypeError("valueRef must be an InlineValueRef or ChunkValueRef.")
    if valueRef.codecId != _JSON_CODEC:
        raise ValueError(f"Unsupported committed-value codec: {valueRef.codecId}.")
    if isinstance(valueRef, InlineValueRef):
        payload = valueRef.payload
    else:
        chunk = store.require(valueRef.chunkId)
        if chunk.contentHash != valueRef.contentHash:
            raise RuntimeError(f"ChunkValueRef integrity metadata mismatch: {valueRef.chunkId}.")
        payload = chunk.payload
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise RuntimeError("Committed ValueRef cannot be decoded.") from err
