# Turnix RPC Protocol — Exhaustive Design Spec (Final Draft, +Validation/Chunking/Diagnostics)

**Scope**: Single-app WebSocket RPC between **Frontend (JS)** and **Backend (Python)**.  
**Goal**: A reliable, retry-aware, transport-aware protocol with object proxying, view lifecycle, rehydration via snapshot, deadlines/extension/cancel, progress reporting, robust errors, **payload validation**, **chunked snapshots**, and **rate/diagnostic metrics**.  
**Audience**: Engine/Core devs, Mod/runtime devs, Frontend infra.

---

## 0) High-level Principles

- **Single-app wire**: Only Turnix uses this socket. Namespaces are flat under Turnix; transport/health is under `system.*`.
- **Backend is authority**: Defaults and execution policy are set server-side.
- **Link-aware reliability**: All ACK/Reply timers **freeze** while the socket is down and **resume** after reconnect.
- **Idempotence via messageId**: Retries reuse the same `messageId`. `retryAttempts` starts at **0**.
- **Source of truth**: Backend owns object state; rehydration is a **full snapshot emit** (or **chunked** as needed).
- **No “ok:false” replies**: Success uses `reply`, failures use `error`.
- **No public unserializable error**: Serialization must be guaranteed; if it fails, treat as internal bug and surface `E_CALL_FAILED`.

---

## 1) Envelope (Wire Message) Format

All frames share the same core shape. Fields not listed under **Kind-specific** are common to every message.

```jsonc
{
  "originSide": "frontend" | "backend",          // For logging/debug; handlers must not modify
  "kind": "emit" | "request" | "reply" | "ack" | "error",
  "messageId": "string",                          // Unique per message; reused when retrying the same message
  "timestampUnixSeconds": 1733469123.456,         // Float seconds, sender’s clock (for logs/latency)
  "retryAttempts": 0,                             // 0 on first send; incremented on resend
  "actionName": "segment.verb",                   // See namespaces below
  "payload": { /* action-specific data only */ }  // Writable by handlers
}
```

**Kind-specific fields**

- **`reply`**:  
  `payload` **must** be `{ "result": <any>, "requestId": "<original messageId>" }`
- **`error`**:  
  `payload` **must** be `{ "error": { "code": "E_*", "message": "string", "details": any }, "requestId": "<original messageId>" }`
- **`ack`**:  
  `payload` **must** be `{ "ackedMessageId": "<messageId being acknowledged>" }`

- For `ack` frames, `actionName` SHOULD mirror the original message’s `actionName` for diagnostics, but is ignored by routers.
- `ack` frames are transport-only and MUST bypass action routing entirely.

**Identity metadata** (`viewId`, `clientId`, `sessionId`, `securityToken`, `objectId`, `modId`) should live in `payload.context` (extension block). If performance or legacy requires, these may temporarily appear at top-level but are considered **extension metadata**, not core protocol.

**Reply vs. Error exclusivity**
- Success and failure frames are **mutually exclusive** for a given `requestId`.
- A `reply` frame **MUST** include `payload.result` and **MUST NOT** include `payload.error`.
- An `error` frame **MUST** include `payload.error` and **MUST NOT** include `payload.result`.
- In both cases, `payload.requestId` **MUST** be present and equal to the original request’s `messageId`.

---

## 2) Namespaces & Routers

Flat, single-app layout with one router per first segment:

- **`system.*`** — transport/health only  
  - `system.heartbeat`
- **`view.*`** — connection lifecycle  
  - `view.bind` (auth/bind; returns `sessionId`)  
  - `view.sync` (request); backend replies, then **emits** one `view.snapshot` **or** chunked series (`view.snapshotChunk` + `view.snapshotComplete`)
  - `view.snapshot` (emit; full, single message)
  - `view.snapshotChunk` (emit; chunked snapshot part)
  - `view.snapshotComplete` (emit; signals snapshot reassembly success)
- **`proxy.*`** — backend object mediation (internal + mods)  
  - `proxy.describe` (ad-hoc; generally provided via snapshot)
  - `proxy.get`
  - `proxy.set`
  - `proxy.call`
  - `proxy.propertyChanged` (emit; backend→frontend)
- **`mods.*`** — mod lifecycle/control (not object access)  
  - `mods.list|getInfo|reload|enable|disable|policy.get|policy.set` (optional)
- **`log.*`** — logging  
  - `log.append` (backend→frontend stream of logs)  
  - `log.configure` (frontend→backend)
- **`job.*`** — execution control  
  - `job.deadline`, `job.extend`, `job.cancel`, `job.status` (optional)
- **`request.progress`** — progress events (emit; backend→frontend)

Unknown action ⇒ **`E_HANDLER_NOT_FOUND`**.

> Note: `kind:"ack"` and `system.heartbeat` are handled by the transport layer and MUST NOT be dispatched to routers/handlers.

---

## 3) Reliability & Transport (Link-Aware)

### 3.1 Connection states (frontend)

- **GREEN**: WebSocket `OPEN`
- **AMBER**: `CONNECTING/RECOVERING`
- **RED**: `CLOSED` or suspected down (e.g., missed heartbeats)

Expose `transportState` and `transportEpoch` (increment per reconnect) for logs/metrics.

### 3.2 Sending & timers

- Maintain an **Outbox**:
  - If **GREEN** → send immediately.
  - If **AMBER/RED** → **enqueue**; do **not** start timers.
- **ACK timers** (for any outbound message) and **Reply timers** (for outbound requests) run **only** when GREEN.
- When state flips to **AMBER/RED**:
  - **Pause** all running timers and record remaining time.
  - Do **not** fire `ack` or `reply` timeouts while offline.
- When state returns to **GREEN**:
  - **Flush** outbox (never-sent messages) with `retryAttempts = 0`.
  - **Re-send** all messages still in **awaitingAck** with the **same `messageId`** and `retryAttempts += 1`.
  - **Resume** paused timers from remaining time.

### 3.3 Sender-side maps

- **`awaitingAck`**: `messageId → { envelope, ackTimer, attempts }`
- **`awaitingReply`**: `requestId → { future/promise, replyTimer, meta }`
- **`completedReplies`**: `requestId → reply|error envelope` (kept until **that** envelope is ACKed)
- **`seenMessageIds`**: dedupe window for inbound frames (by `messageId`)
- **`settledRequestIds`**: window of `requestId`s whose reply/error has been ACKed

### 3.4 Receiver-side maps (for inbound requests)

- **`currentlyExecutingRequests`**: requestId → { startedAt, deadlineAt, cancelToken, … }  
  (Only ACKed **inbound** requests in progress.)
- **`completedReplies`**: requestId → reply|error envelope (kept until reply’s ACK)

**Duplicate handling**

- Duplicate **request**: ACK again; if running, **do not re-execute**; if completed, re-send cached reply/error.
- Duplicate **emit**: ACK again; optional drop by `seenMessageIds`.
- Duplicate **reply/error**: ACK again; if original already settled, drop.

### 3.5 Heartbeat semantics

- `system.heartbeat` is an `emit` used purely for liveness/latency and MUST be **transport-handled** (not routed to handlers).
- Each side SHOULD send heartbeats at the configured interval; receivers **ACK** them as with any `emit`.
- If **N=3** consecutive expected heartbeats are missed, mark the link **RED**, freeze timers and outbox.
- On the first successful heartbeat exchange after reconnect (link returns **GREEN**), bump `transportEpoch`, log the transition, and resume timers / flush the outbox.

---

## 4) Timings & Defaults (starting points)

- **ACK timeout** (`ackTimeoutSeconds`): 5s
- **ACK retries** (`maxAckRetries`): 3
- **Reply timeout** (`replyTimeoutSeconds`): 10s (caller may slide on progress up to a cap)
- **Heartbeat**: every 5–10s; declare RED after 3 misses
- **Reconnect backoff**: 1s, 2s, 4s, 8s… capped at 15–30s

**Server Policy (authoritative, backend-provided):**
- `defaultActionDeadlineSeconds`: 30 (or 60)
- `extendActionExecutionSeconds`: 20–30
- `extensionResponseTimeoutSeconds`: 10
- `maxMessageBytesInbound`: 1 * MiB (close with 1009-style error on oversize)
- `chunkTargetBytes`: 512 * KiB
- `maxInFlightChunks`: 4
- `minInFlightChunks`: 1
- `metrics.enabled`: true in dev, false in prod by default
- `dedupWindowSeconds`: 60
- `dedupMaxEntries`: 2000
- `minProgressIntervalSeconds`: 0.5
- `defaultProgressIntervalSeconds`: 1.0
- `ackLatencyAmberP95Ms`: 300
- `ackLatencyCriticalP95Ms`: 800
- `chunkWindowRecoveryStableWindows`: 5
- `chunkWindowDegradeUnstableWindows`: 3
- `schemaValidationMode`: `"strict"` (server), `"warn"` or `"off"` (client)

> If `ackLatencyP95` remains AMBER for N windows, shrink `maxInFlightChunks` by 1 down to 1.

---

## 5) Lifecycle & Rehydration

### 5.1 Bind & Sync

1) **`view.bind`** (request)  
   Input: `{ context: { viewId, clientId, securityToken? } }`  
   Reply: `{ sessionId }`

2) **`view.sync`** (request)  
   Reply: `{ view: { viewId, sessionId, focused? } }`  
   Then backend emits one of:
   - **`view.snapshot`** (single message), or
   - **`view.snapshotChunk`** *(N parts)* followed by **`view.snapshotComplete`** (chunked mode).

### 5.2 `view.snapshot` (emit)

A **full** descriptor + state dump for **owned** objects.

### Example shape (full snapshot)
```jsonc
{
  "view": { "viewId": "v-main", "sessionId": "s-123" },
  "generatedAtUnixSeconds": 1733469123.000,
  "objects": [
    {
      "objectId": "Author@Mod:Thing#42",
      "typeName": "Thing",
      "objectVersion": 7,
      "descriptor": {
        "doc": "Class docstring…",
        "flags": { "policy": "denylist" | "allowlist" },
        "properties": [
          { "name": "title", "readonly": false, "doc": "…", "typeHint": "str" },
          { "name": "count", "readonly": true,  "doc": "…", "typeHint": "int" }
        ],
        "methods": [
          { "name": "doWork", "doc": "…", "params": [{ "name": "x", "type": "int" }], "returnTypeHint": "bool" }
        ]
      },
      "state": { "title": "Hello", "count": 5 }
    }
  ]
}
```

### 5.3 Chunked snapshot (emit)

Use when the full snapshot would exceed `maxMessageBytesInbound` or desired target size.

- **`view.snapshotChunk` payload**:
  ```jsonc
  {
    "snapshotId": "uuid",
    "partIndex": 0,
    "totalParts": 8,
    "bytesTotal": 7340032,
    "sha256": "<hex of entire snapshot>",
    "partKind": "json",
    "partJson": { /* subset of objects array or sliced json */ }
  }
  ```
  - If sending raw bytes, use `"partKind":"bytes"` and `"partBytes":"<base64>"`.
- **`view.snapshotComplete` payload**:
  ```jsonc
  {
    "snapshotId": "uuid",
    "objectsCount": 123,
    "bytesTotal": 7340032,
    "sha256": "<hex>"
  }
  ```

**Flow & reliability**
- Normal emit → ACK rules per **chunk**. Duplicates must be re-ACKed and ignored if already applied.
- Receiver assembles parts by `snapshotId`. Only after all parts present and `sha256` matches do we apply the snapshot atomically, then treat it as if `view.snapshot` arrived.
- Allow up to `maxInFlightChunks` outstanding; sender gates by ACK progress.

> If reassembly fails (missing part, mismatched `sha256`, or corrupted base64), receiver MUST discard the snapshot buffer and raise `error { code: "E_CONFLICT" }`.

### 5.4 Live updates

- **`proxy.propertyChanged`**: `{ objectId, propertyName, newValue, objectVersion }` — apply only if version increases.
- Reuse proxy instances across rehydrate; update in place.

---

## 6) Proxy Access & Policy

- **Policy modes**
  - **Core/internal objects**: `policy: "allowlist"` (closed by default; curated exposes)
  - **Mod objects**: `policy: "denylist"` (open by default; conservative denies)

- **Pattern grammar**
  - **Glob** via `fnmatch` semantics: `*`, `?`, `[abc]`, etc.
  - **Regex literal**: `/…/` interpreted as **unanchored** search (`re.search`).
  - **Top-level names only**: no dotted paths.
  - Normalize (trim/drop empties).

- **Precedence & private names**
  - `_`/`__` names are **denied by default** unless present in **allow** list.
  - Resolution:
    - If `allowlist`: must match **allow**; **deny** can prune; else deny.
    - If `denylist`: default allow; **deny** blocks; **allow** can explicitly grant (still subject to deny).
  - **Deny wins**.

- **Methods & properties**
  - `proxy.describe` returns filtered descriptor (dev-only `policyPreview` when requested).
  - `proxy.call` `{ objectId, methodName, args: [], kwargs: {} }` → `{ result }` (await coroutines; offload CPU-bound).
  - `proxy.get` `{ objectId, propertyName }` → `{ value }` (never return live callables).
  - `proxy.set` `{ objectId, propertyName, value }` → `{ ok: true, newValue, objectVersion }` (read-after-write echo; CAS optional).

**Args/kwargs rules**
- Clients MAY send both `args: []` and `kwargs: {}`.
- If a parameter is supplied positionally in `args` **and** by name in `kwargs`, the call is **ambiguous** and MUST be rejected with `E_INVALID_PAYLOAD`.
- Servers SHOULD surface a concise error path (e.g., `"params[0]" and "kwargs.x" both provided"`).
- Implementations MAY support keyword-only or variadic parameters; when unsupported, reject with `E_INVALID_PAYLOAD`.

**Authoritative write-back:** `proxy.set` replies include `{ newValue, objectVersion }`.
- The **server is authoritative**. Clients MUST replace any local cache with `newValue` regardless of what was attempted.
- If the server normalizes/coerces values (e.g., clamping numbers, trimming strings), the reply reflects the canonical value.

**Optional CAS write mode (forward-compatible):**
- Clients MAY send an optimistic write using `writeMode:"compareAndSet", expectedVersion:<int>`.
- The server MAY reply `E_CONFLICT` if `objectVersion` has advanced; clients SHOULD refresh and retry.

- **Reflection guardrails (Python)**
  - Skip `_`/`__` before touching attributes; guard `getattr`; compute writability safely (`property.fset is None`, etc.).

- **Ownership & security**
  - Registry stores `owner: { viewId, clientId, sessionId }`; only owner may act unless shared.
  - `proxy.*` must originate from a bound session.

---

## 7) Execution Deadlines, Extension & Cancel

**Backend policy keys (authoritative):**

- `defaultActionDeadlineSeconds` (e.g., 30 or 60)
- `extendActionExecutionSeconds` (e.g., 20–30; used when extension amount not specified)
- `extensionResponseTimeoutSeconds` (e.g., 10)

**Per-request hint**:

- `payload.deadlineSeconds` (starts at **ACK time**). If absent, use `defaultActionDeadlineSeconds`.

**Runtime:**

- On receiving a **request**: ACK immediately; compute `deadlineAt = now + effectiveDeadline`.
- Store `{ deadlineAt, cancelToken }` in `currentlyExecutingRequests`.

**When deadline hits**:

1) Emit **`job.deadline { requestId, elapsedSeconds, limitSeconds }`**.  
2) Wait up to `extensionResponseTimeoutSeconds` for:
   - **`job.extend { requestId, extendSeconds? }`**  
     → New `deadlineAt += extendSeconds || extendActionExecutionSeconds`.
   - **`job.cancel { requestId, reason? }`**  
     → Cancel job; send `error { code: "E_CANCELLED_BY_USER_DEADLINE_EXCEEDED" }`.
3) If no response: cancel with **`E_DEADLINE_EXCEEDED`**.

**Caller-side behavior on long waits**:

- Slide local **reply timeout** on progress events (below) up to a max patience cap, or:
- Re-send original request (same `messageId`), or:
- Ask `job.status { requestId }` (optional), or:
- User-initiated `job.cancel` / `job.extend`.

**Cancellation races**:

- If `job.cancel` arrives after completion, return **`E_CANCELLING_FINISHED_JOB`** (or no-op if reply already ACKed).

---

## 8) Progress Reporting

- **Opt-in** per request: `payload.reportProgress: true`, optional `payload.progressIntervalSeconds`.
- Backend injects a reporter; runtime throttles to the interval (+ jitter).
- Emit **`request.progress { requestId, percent?, stage?, etaSeconds? }`** (emit; ACKed).
- Caller may **ignore** progress; or use it to slide local reply timeout.

**Rate clamp:** To avoid chatty loops, the backend MUST enforce a minimum interval between progress emits.
- `progressIntervalSeconds` from the caller is treated as a **hint**.
- The server applies `minProgressIntervalSeconds` (policy default: **0.5s**) and uses `max(progressIntervalSeconds||policyDefault, minProgressIntervalSeconds)`.
- Bursty reporters SHOULD be debounced on the server side.

**100% is not completion**
- `percent: 100` MAY be emitted before the final `reply` (e.g., during post-processing/finalization).
- The **authoritative completion signal** is the `reply` (or `error`) frame.
- After `percent: 100`, emit only `stage`/`etaSeconds` if relevant; avoid oscillating `percent` values.

**Server Policy keys**
- `minProgressIntervalSeconds`: 0.5
- `defaultProgressIntervalSeconds`: 1.0

---

## 9) Errors (Canonical Set)

Transport/exec:
- `E_DEADLINE_EXCEEDED`
- `E_CANCELLED`
- `E_CANCELLED_BY_USER_DEADLINE_EXCEEDED`
- `E_UNAVAILABLE` (e.g., server overloaded; max concurrent executions reached)
- `E_CANCELLING_FINISHED_JOB`

Proxy/system:
- `E_FORBIDDEN`
- `E_NO_SUCH_OBJECT`
- `E_NO_SUCH_PROPERTY`
- `E_NO_SUCH_METHOD`
- `E_READONLY_PROPERTY`
- `E_CALL_FAILED` (sanitized message)
- `E_CONFLICT` (e.g., CAS mismatch)
- `E_HANDLER_NOT_FOUND`
- `E_INVALID_PAYLOAD` (schema validation failure)

**Reply shape (error)**:
```jsonc
{
  "kind": "error",
  "messageId": "...",
  "requestId": "<original requestId>",
  "payload": { "error": { "code": "E_*", "message": "string", "details": any } }
}
```

---

## 10) Serialization

- **Python (backend)**: Python 3.12, Pydantic v2 models for envelopes/payloads; `model_dump(mode="json")` with custom encoders:
  - `datetime` → ISO 8601
  - `Decimal` → string
  - `bytes`/buffers → base64 (or avoid)
  - Other complex types → return **handles** (`objectId`) not raw data

- **SchemaRegistry (server)**: `actionName` (+ optional `payloadVersion`) → Pydantic model; invalid → `E_INVALID_PAYLOAD` with concise errors.

- **Schema versioning:** the SchemaRegistry may route by `(actionName, payloadVersion?)`; if `payloadVersion` is omitted, use the current model.

- **JavaScript (frontend)**: custom JSON serializer:
  - `Map` → array of `[key,value]` (or object if all keys are strings)
  - `Set` → array
  - `Date` → ISO string
  - `BigInt` → string
  - Typed arrays / ArrayBuffer → base64 or hex

- **No public `E_UNSERIALIZABLE`**. Failures are internal bugs; surface `E_CALL_FAILED` and fix encoders.

---

## 11) Logging

Adopt concise, structured tags (examples):

```
[rpc] send request id=<id> action=<action> retry=<n> epoch=<k>
[rpc] recv ack ackedMessageId=<id> action=<action>
[rpc] recv reply id=<replyId> requestId=<id> action=<action> ok
[rpc] recv error requestId=<id> code=<E_*> message="<...>"
[rpc] resend awaitingAck id=<id> retry=<n>
[rpc] link state RED→GREEN epoch=<k>
```

- Sender keeps a cache of replies/errors until the reply’s ACK arrives.

Frontend may also receive:
- `log.append { level, logger, message, fields?, timeIso }`
- Configure via `log.configure { level?, categories?, redact? }`

## 11.1 Diagnostics & Rate Monitoring

**Counters:** tx/rx messages, tx/rx bytes, retries, ack timeouts, reply timeouts, dedup hits, outbox depth, heartbeat misses, snapshot parts sent/recv.  
**Latency:** ACK and Reply p50/p95.  
**Windows:** EWMA(1s/10s/60s).  
**Backpressure (optional):** if `awaitingAck + awaitingReply` exceeds a threshold → warn and delay new sends; if `ackLatencyP95` spikes → mark link AMBER and shrink `maxInFlightChunks`.  
**Exposure:** `/turnix/metrics` or dev console; default enabled in dev, off in prod unless explicitly turned on.
**Chunk window backpressure (example policy):**
- Compute `ackLatencyP95` over rolling 1s/10s/60s EWMAs.
- If `ackLatencyP95` ≥ `ackLatencyCriticalP95Ms` for `chunkWindowDegradeUnstableWindows` consecutive windows:
  - Reduce `maxInFlightChunks` by 1 (never below `minInFlightChunks`).
- If link remains stable (`ackLatencyP95` < `ackLatencyAmberP95Ms`) for `chunkWindowRecoveryStableWindows` consecutive windows:
  - Increase `maxInFlightChunks` by 1 (capped at policy `maxInFlightChunks`).
- Log each change: `[rpc] chunk-window adjust from=<n> to=<m> reason=<latency|recovery>`.

---

## 12) JS Facades (Auto-Proxy Builder)

**Construction**: From `view.snapshot` (no per-object `describe` round-trips).
- Build a **proxy object** per `objectId`.
- Attach **methods** that call `proxy.call` with `{ args, kwargs }`.
- Maintain a **local state cache** from snapshot `state`.
- Getter reads return from cache (sync).  
  Provide `proxy.$refresh()` to re-pull via `proxy.get`.
- Setter writes use `proxy.set` and update cache from `{ newValue, objectVersion }`.
- Reuse instances across rehydrate; update descriptor/state **in place**.
- Enforce **version ordering** when applying `proxy.propertyChanged`.

> `proxy.set`: server is authoritative - client cache should always replace local value with newValue.

**Optional niceties**:
- `proxy.$on('change', handler)` to observe local state updates.
- Method kwargs ergonomics if descriptor advertises named params.
- (Build step) Generate **TypeScript types** from Pydantic/descriptor for DX/autocomplete.

---

## 13) Security & Ownership

- **Binding**: `view.bind` authenticates with `{ viewId, clientId, securityToken }` and returns `sessionId`.
- **Per-connection ownership**: only bound sessions can operate on their objects, unless explicitly shared.
- **TLS** for WS; assume socket identity is not spoofable. On reconnect, require `view.bind` again and then `view.sync`.
- **Capabilities**: reserved as an **internal** layer (per principal) to gate sensitive operations; not exposed on wire yet.

---

## 14) Resource Control

- Cap **concurrent executions** per `{ viewId, clientId, sessionId }` (simple semaphore).
- On overflow: early `error { code: "E_UNAVAILABLE" }`.
- Backpressure options (future): rate limits per connection, queueing with `accepted`/`jobId` (see Extensions).

---

## 15) Optional Extensions (Forward-compatible)

- **Accepted/Job model**: Add `kind: "accepted"` and a `jobId` for long work; progress/deltas keyed by `jobId`.
- **Delta rehydrate**: `view.sync { sinceSnapshotId? }` and `view.delta` emits.
- **Binary frames**: for large payloads; headers/body split or base64 today.

---

## 16) Test & Validation Checklist

**Reliability**
- Duplicate `request` → re-ACK, **no re-execute**; if completed, re-send cached reply/error.
- Missing ACK → resend until `maxAckRetries`, then fail pending (if any).
- Reply timeout (caller) → choose **resend**, **job.status**, or **cancel**; ignore late replies after resolve (still ACK for sender’s cleanup).
- Link down during wait → timers **freeze**; upon reconnect timers **resume** and outbox **flushes**.

**Lifecycle**
- `view.bind` must precede object operations; reject pre-bind with `E_FORBIDDEN`.
- `view.sync` reply followed by a single `view.snapshot` emit.
- Snapshot application builds or updates facades and cache; deltas apply by version.

**Proxy/policy**
- `proxy.describe` filters per policy; docstrings/type hints present when available.
- Private names filtered by default (`_`, `__`).
- `proxy.get` never returns a callable.
- `proxy.set` echoes `{ newValue, objectVersion }`.
- CAS mode returns `E_CONFLICT` on mismatch.

**Deadlines**
- Per-request `deadlineSeconds` honored; defaults taken from backend policy.
- On hit: `job.deadline` emitted; extend/cancel honored; else `E_DEADLINE_EXCEEDED`.
- Cancellation race returns `E_CANCELLING_FINISHED_JOB`.

**Errors**
- Unknown action → `E_HANDLER_NOT_FOUND`.
- Forbidden → `E_FORBIDDEN`.
- Missing member → `E_NO_SUCH_PROPERTY`/`E_NO_SUCH_METHOD`.
- Handler error → `E_CALL_FAILED` with sanitized message.
- Wrong Payload model shape → `E_INVALID_PAYLOAD`
