# Turnix Tracing Standard v0.1

This document defines the **wire format**, **conceptual model**, **lifecycle rules**, and **required fields** for the Turnix tracing system.  
It is the canonical reference for all tracing in pre-alpha.

Tracing is used to understand:

- what happened inside a pipeline run  
- how RPC, mods, memory, and LLM calls interact  
- performance, ordering, and concurrency  
- debugging unexpected updates in memory  
- identifying stuck or unfinished operations  

No backward compatibility is guaranteed in `0.x` versions.

---

# 1. Core Concepts

Turnix tracing uses **spans** and **events**.

- A **span** represents an operation with a start and (usually) an end.
- An **event** is a point-in-time record that belongs to a span.
- Missing `spanEnd` means the span is still running or ended unexpectedly.

All trace data is sent as **individual JSON objects** through a single stream (e.g. `/ws/trace`), one per message.

---

# 2. Span Model

A span has:

- `spanId` — unique identifier  
- `traceId` — correlates a full flow (RPC → pipeline → LLM)  
- `parentSpanId` — null for roots  
- `spanName` — stable descriptive name  
- `status` on completion:
  - `"ok"`  
  - `"error"`  
  - `"cancelled"`  
- optional error fields:
  - `errorType`
  - `errorMessage`
  - `errorStack`

Spans emit:

- a `spanStart` record at the moment they begin  
- a `spanEnd` record when they finish  

If a span never ends, it remains open in the viewer.

---

# 3. Event Model

Events:

- belong to a span (`spanId`)
- have `eventName`
- have structured `attrs`
- are point-in-time, not durations

All errors that end a span produce **both**:

1. a `spanEnd` with `"status": "error"`
2. optionally one or more `"event"` records with additional diagnostic data

---

# 4. Record Types

Each JSON record sent through the trace stream has:

```python
recordType: "spanStart" | "spanEnd" | "event"
```

Additional global fields:

- `time`: ISO 8601 timestamp `("2025-11-15T12:34:56.789Z")`
- `seq`: monotonic incrementing integer for ordering
- `traceId`
- `spanId`
- `level`: `"debug" | "info" | "warn" | "error"`
- `tags`: list of short strings
- `attrs`: structured metadata for this record

All records may include context fields where applicable.

---

# 5. Context Fields

Optional standardized names:

- `appInstanceId`
- `sessionId`
- `pipelineId`
- `pipelineRunId`
- `viewId`
- `clientId`
- `modId`
- `hookId`
- `rpcKind` — `"inbound"` / `"outbound"`
- `llmProvider` — e.g. `"llama.cpp"`
- `llmPreset` — profile/preset name used by the LLM adapter

These fields allow filtering and correlation across subsystems.

---

# 6. spanStart Record

A `spanStart` record includes:

```json
{
  "recordType": "spanStart",
  "time": "...",
  "seq": 0,
  "traceId": "...",
  "spanId": "...",
  "parentSpanId": null,
  "spanName": "pipeline.run",
  "appInstanceId": "...",
  "sessionId": "...",
  "pipelineRunId": "...",
  "level": "info",
  "tags": ["pipeline"],
  "status": null,
  "attrs": {
    "entryPoint": "user.chat"
  }
}
```

Rules:
- `spanStart` is emitted **immediately** when the operation begins.
- `status` is always `null` on start.
- `spanName` is stable and does not encode identifiers.

---

# 7. spanEnd Record

```json
{
  "recordType": "spanEnd",
  "time": "...",
  "seq": 1,
  "traceId": "...",
  "spanId": "...",
  "spanName": "pipeline.run",
  "appInstanceId": "...",
  "sessionId": "...",
  "pipelineRunId": "...",
  "level": "info",
  "tags": ["pipeline"],
  "status": "ok",
  "errorType": null,
  "errorMessage": null,
  "errorStack": null,
  "attrs": {
    "durationMs": 333.2
  }
}
```

Rules:
- emitted when span completes normally or abnormally
- `status` must be `"ok"`, `"error"`, or `"cancelled"`
- error fields must be provided for `"error"`
- viewers treat missing `spanEnd` as "open/unfinished"

---

# 8. event Record

```json
{
  "recordType": "event",
  "time": "...",
  "seq": 2,
  "traceId": "...",
  "spanId": "spn-123",
  "eventName": "rpc.recv",
  "appInstanceId": "...",
  "sessionId": "...",
  "level": "debug",
  "tags": ["rpc", "inbound"],
  "attrs": {
    "method": "pipeline.startRun",
    "payloadSize": 852
  }
}
```

Rules:
- `event` records always attach to a span
- no `status` field
- used for fine-grained logging:
  - RPC framed in/out
  - hook.enter / hook.exit
  - llm.request / llm.chunk / llm.response
  - memory.before / memory.after / memory.diff
  - pipeline.start / pipeline.end

---

# 9. Standard Span Names

These are canonical:
- `"process.turnix"`
  - lifecycle of the backend process
  - emits `process.start` and `process.stop` events
- `"appInstance.lifecycle"`
  - one per AppInstance instance
- `"session.lifecycle"`
  - represents a single session
- `"pipeline.run"`
  - one per pipeline execution
- `"rpc.frame"`
  - one per inbound or outbound frame
- `"mod.hook"`
  - each mod hook invocation
- `"llm.call"`
  - one model request-response cycle
- `"memory.op"`
  - any create/update/delete/load operation

Span names do **not** include identifiers.
Identifiers go into `attrs` and context fields.

---

# 10. Memory Operations

Memory operations use the same tracing system:

### Span
```python
spanName: "memory.op"
```

### Required attrs
- `opKind`: `"create" | "update" | "delete" | "load"`
- `layerId`: memory layer name (`"party"`, `"world"`, `"session"`)
- `itemRef`: logical identifier of the QueryItem (e.g.`"npc.bartender"`)

### Optional diff attrs

- `fieldsChanged`
- `hash.before` / `hash.after`
- `textPreview.before` / `textPreview.after`

Memory viewer apps use these to reconstruct history, while backend RPC gives the current state.

---

# 11. Lifecycle Rules

### Spans
- must emit `spanStart` immediately.
- must emit `spanEnd` when finishing.
- missing `spanEnd` means still running or abnormal termination.
### End status
- `"ok"` — normal completion
- `"error"` — raised exception, with error fields
- `"cancelled"` — terminated intentionally
### Events
- do not affect span lifecycle.
- used for details inside spans (RPC chunks, hook calls, memory diffs).

---

# 12. Transport and Storage

This spec only defines the **format** of records.

Delivery is handled by:

- a TraceHub inside backend
- in-memory ring buffer
- live streamed trace messages via WebSocket `/ws/trace`
- optional viewer-side file save/load

Backend tracing must never block or throw.

---

# 13. Versioning

This is **Turnix Tracing Standard v0.1**.

- Pre-alpha: breaking changes allowed, no legacy support.
- After 1.0: schema becomes stable.
