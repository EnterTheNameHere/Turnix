# Turnix RPC: Proxy, Exposure & Rehydration Plan

**Status:** Draft (ready to implement)\
**Audience:** Engine/Core devs, Mod/runtime devs\
**Goal:** Lock the wire actions and finalize the exposure model (denylist-by-default for mods, allowlist for core), add proxy primitives, and define reconnect/rehydration.

---

## 1) Scope & Design Goals

- Keep the **wire envelope** unchanged (compat with existing `emit`/`request`/acks).
- Add **proxy primitives** so JS facades can access Python-owned objects safely.
- Use **denylist-by-default for Mods** (friendly), **allowlist for Core** (safe).
- Make **reconnect + rehydration** a first-class flow (backend is the source of truth).
- Preserve **symmetry**: identical reliability semantics on both sides (JS, Python).

---

## 2) Wire Contract (Actions & Envelope)

We keep a single envelope for every message kind:

```jsonc
{
  "messageId": "uuid",
  "correlationId": "uuid-or-null",   // reply ties to request
  "kind": "emit|request|reply|acknowledgeReceipt|acknowledgeResult|error|heartbeat",
  "actionName": "system.initializeView|proxy.call|mods.someMod.doThing|...",
  "timestamp": 1733760000000,
  "retryAttemptNumber": 0,
  "origin": { "side": "frontend|backend", "clientId": "...", "viewId": "..." },
  "dataPayload": { /* action-specific data only */ }
}
```

### New/confirmed action namespaces

- `system.*`: `initializeView`, `rehydrateView`, `heartbeat`.
- `proxy.*`: `call`, `get`, `set`, `describe`.
- `mods.<modId>.*`: mod buses and ad‑hoc actions.

> Handlers may read core fields but **must not modify** them. Only `dataPayload` is writable by handlers.

---

## 3) Reliability Semantics (unchanged)

- **Emit flow**: sender → `emit` → receiver immediately sends `acknowledgeReceipt` → handler executes → (no reply expected).
- **Request/Reply flow**: `request` → `acknowledgeReceipt` → handler executes → `reply` → origin sends `acknowledgeResult` → settle.
- **Ack retry**: if receipt not received by `ackTimeoutMs`, resend with `retryAttemptNumber+1` until `maxAckRetries`. Then fail.
- **Reply timeout**: if no `reply` by `replyTimeoutMs`, optionally `emit system.requestTimedOut` (best‑effort), then reject.
- **Dedup/replay**: per `messageId` (and per `correlationId` for settled replies). Duplicates trigger re‑ack and no re‑execution.
- **Heartbeat**: periodic `heartbeat` `emit` (both directions) for liveness and latency.

> Timing defaults live in code; the protocol is agnostic. Keep both sides symmetrical.

---

## 4) Routing

Dispatch by `actionName` prefix:

- `system.` → SystemRouter
- `proxy.` → ProxyRouter
- `mods.`   → ModsRouter (per‑mod bus)
- default   → error reply `HANDLER_NOT_FOUND`

---

## 5) Exposure Model

Two modes:

### 5.1 Mods (default‑open, **denylist by default**)

Everything exported by a mod’s API surface is callable/gettable/settable **unless denied** by rules. Default denies:

- **Private names**: `/^_|^__/` (methods and properties)
- **Known‑dangerous** patterns (examples): `*BackendHook`, `*Internal*`, `getTurnix*`
- **Setter guards** (optional): disallow writes to `_*`, `state*`, etc.

Mod authors do **not** need to configure anything. Defaults are safe and permissive.

### 5.2 Turnix Core (default‑closed, **allowlist**)

Core/engine objects expose **only** a curated set of methods/properties. This keeps sharp tools private.

### 5.3 Object‑level overrides

Each backend receiver can attach an `exposure` config (wildcards supported):

```py
exposure = {
  "denyCall": ["registerBackendHook", "destroyAll*", "_*"],
  "denyGet":  ["secret*", "__*"],
  "denySet":  ["state*", "_*"],
  // Core can invert policy by supplying explicit allow lists instead:
  "allowCall": ["registerHook", "emitEvent"],
  "allowGet":  ["id", "name"],
  "allowSet":  ["title"]
}
```

Resolution order:

1. If allowlists exist → evaluate **allow** first, then apply deny to prune.
2. If no allowlists → default‑open for mods with deny rules applied.

### 5.4 Decorators (optional sugar)

- `@expose`, `@noexpose`, `@readonly` may be used on server methods/props to tweak exposure without editing the dict.

---

## 6) Proxy Primitives

### 6.1 `proxy.describe`

Returns a **filtered descriptor** for building JS facades.

**Request**

```json
{
  "objectId": "Session:abcd"
}
```

**Reply (ok)**

```json
{
  "ok": true,
  "objectId": "Session:abcd",
  "typeName": "Session",
  "methods": ["registerHook", "emitEvent", "close"],
  "readableProps": ["id", "name", "viewId"],
  "writableProps": ["title"],
  "flags": { "policy": "denylist" }
}
```

### 6.2 `proxy.call`

```json
{
  "objectId": "Session:abcd",
  "methodName": "registerHook",
  "args": ["onTick", {"rate": 1000}]
}
```

Reply (success): `{ "ok": true, "returnValue": null }`\
Reply (forbidden): `{ "ok": false, "errorCode": "Forbidden", "errorMessage": "..." }`

### 6.3 `proxy.get`

```json
{ "objectId": "Session:abcd", "propertyName": "name" }
```

Reply: `{ "ok": true, "value": "Main Session" }`

### 6.4 `proxy.set`

```json
{ "objectId": "Session:abcd", "propertyName": "title", "value": "Act I" }
```

Reply: `{ "ok": true }`

---

## 7) System Actions

### 7.1 `system.initializeView`

Binds identity and authenticates the socket.

**Request**

```json
{
  "clientId": "c-123",
  "viewId":   "v-main",
  "securityToken": "signed.jwt.or.nonce"
}
```

Reply: `{ "ok": true }` (plus `acknowledgeReceipt`/`acknowledgeResult` around it as usual)

### 7.2 `system.rehydrateView`

JS calls this after (re)connect. Backend returns snapshot and object handles.

**Reply (ok)**

```json
{
  "ok": true,
  "view": { "viewId": "v-main", "sessionId": "s-main", "focused": true },
  "objects": [
    { "objectId": "Session:s-main", "typeName": "Session" },
    { "objectId": "View:v-main",    "typeName": "View" }
  ]
}
```

JS then calls `proxy.describe` for each and rebuilds facades.

---

## 8) Capabilities (Optional, Extensible)

Grant fine‑grained privileges without widening Core allowlists.

```py
session.grantCapabilities(viewId="v-main", objectId="Session:s-main",
                          caps=["registerHook", "writeMemory"], ttl=600)
```

Receiver checks the call context (origin, viewId, clientId, modId) for caps before allowing sensitive methods. Revoked on disconnect or expiry.

---

## 9) Reconnect & Rehydration Flow

1. Socket drops; JS tears down timers, rejects in‑flight promises.
2. JS reconnects (`ResilientWebSocket`), sends `system.initializeView`.
3. JS requests `system.rehydrateView`; backend returns snapshot + handles.
4. JS issues `proxy.describe` for each handle; rebuilds proxies.
5. Normal traffic resumes.

> Backend is the source of truth; any in‑flight server work from the old socket is failed or resumed only if the operation is idempotent.

---

## 10) Errors & Logging

**Canonical error codes**: `Forbidden`, `NotFound`, `Timeout`, `InvalidPayload`, `HandlerError`, `Internal`, `HANDLER_NOT_FOUND`.

**Reply shape (error)**

```json
{ "ok": false, "errorCode": "Forbidden", "errorMessage": "..." }
```

**Log tags** (examples):

- `[rpc] send request <id> <action> (retry=<n>)`
- `[rpc] recv ack <id>`
- `[rpc] reply <corrId> ok|error:Forbidden`

---

## 11) Implementation Order (Minimal Churn)

1. **Spec**: add the four proxy actions; add exposure section; keep heartbeat unchanged.
2. **Server**: `ReceiverBase` (canCall/get/set; wildcard deny; optional allow); `ActionRouter`.
3. **Server**: `system.initializeView`, `system.rehydrateView`.
4. **Server**: `proxy.describe/call/get/set` + error mapping.
5. **Client**: auto‑proxy builder using `proxy.describe`; integrate into existing connect/reconnect path.
6. **Tests**: protocol engine (acks/retries/timeouts/dedup) + Turnix integration (boot, rehydrate, forbidden calls).
7. **Polish**: decorators, capabilities skeleton, logging tags.

---

## 12) Test Checklist

- Duplicate `emit`/`reply` → re‑ack, no re‑execute.
- Missing receipt → resend until `maxAckRetries`, then fail.
- Reply timeout → optional `requestTimedOut`, then reject.
- `proxy.describe` hides denied members; private names filtered.
- Denied `proxy.call|get|set` → `Forbidden`.
- Reconnect → `rehydrateView` + `describe` → proxies rebuilt; calls succeed.
- Unknown action → `HANDLER_NOT_FOUND` error reply.

---

## 13) Future Extensions

- Backpressure (rate limit per socket; `busy` replies).
- Delta rehydration (only changed handles).
- Snapshotted transactions for resumable server work.
- Binary frames for large payloads (later; protocol‑compatible).

---

## 14) Glossary

- **Receiver**: Python object that mediates RPC access to a real backend object.
- **Facade/Proxy**: JS stub that exposes the same API, forwarding operations over RPC.
- **Exposure**: The policy (deny/allow rules) defining the visible surface of a receiver to JS.
- **Capability**: A scoped privilege bound to origin (view/client/mod) enabling sensitive operations.

