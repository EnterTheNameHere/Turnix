# RPCMessage Schema

This document summarizes the structure of the RPC messages exchanged between the
frontend and backend.  The canonical model lives on the backend in
`backend/rpc/models.py` and is mirrored on the frontend in
`frontend/assets/types.d.ts` and the RPC client helper.

## Core models

| Model | Purpose |
| ----- | ------- |
| `Gen` | Wraps the connection generation (`num`, `salt`) assigned by the server. It is embedded in every RPC envelope so both sides can detect stale traffic.【F:backend/rpc/models.py†L12-L20】 |
| `Route` | Points a message to either a capability (`capability`) or an object (`object`). The backend infers the default lane from these values when the explicit `lane` is missing.【F:backend/rpc/models.py†L24-L77】 |
| `RPCMessage` | The wire envelope for all RPC communication, enforcing camelCase aliases, forbidding extra fields, and performing lane inference via a model validator.【F:backend/rpc/models.py†L31-L79】 |

## Envelope fields

The table below lists every field carried by an `RPCMessage`.  Unless stated
otherwise, the field is optional and defaults to `null`/`None`.

| Field | Type | Description |
| ----- | ---- | ----------- |
| `v` | `str` | Schema version (`"0.1"`).【F:backend/rpc/models.py†L39-L40】 |
| `id` | `str` | UUIDv7 that uniquely identifies the message.【F:backend/rpc/models.py†L40】 |
| `type` | enum | Message category. Valid values include control (`hello`, `welcome`, `clientReady`, `heartbeat`, `ack`) and workload messages (`request`, `emit`, `reply`, `subscribe`, `stateUpdate`, `unsubscribe`, `cancel`, `error`).【F:backend/rpc/models.py†L41-L57】【F:frontend/assets/types.d.ts†L4-L36】 |
| `correlatesTo` | `str` | Links to the parent message (e.g., replies, ACKs, cancels).【F:backend/rpc/models.py†L42】 |
| `gen` | `Gen` | Server-assigned generation, echoed by the client to validate session continuity.【F:backend/rpc/models.py†L43】 |
| `ts` | `int` | Monotonic send timestamp populated on creation.【F:backend/rpc/models.py†L44】 |
| `lane` | `str` | Delivery lane. Defaults to `"sys"` for control traffic; otherwise inferred from `route` (`cap:<capability>` / `obj:<id>`) when empty.【F:backend/rpc/models.py†L59-L77】【F:backend/rpc/messages.py†L39-L67】【F:frontend/assets/rpc-client.js†L1075-L1094】 |
| `budgetMs` | `int` | Optional time budget for the operation. Builders pick defaults from protocol settings and priority classes.【F:backend/rpc/models.py†L45】【F:backend/rpc/messages.py†L39-L67】【F:frontend/assets/rpc-client.js†L952-L999】【F:frontend/assets/rpc-client.js†L1192-L1199】 |
| `ackOf` | `int` | Reserved for richer ACK semantics; currently unused but part of the schema.【F:backend/rpc/models.py†L46】 |
| `job` | `dict` | Mirrors backend job progress snapshots when long-running work reports state.【F:backend/rpc/models.py†L47】 |
| `idempotencyKey` | `str` | Enables backend deduplication and cached replies; defaults to the message ID for request/emit messages.【F:backend/rpc/models.py†L48】【F:frontend/assets/rpc-client.js†L952-L990】 |
| `route` | `Route` | Target capability or object; mandatory for requests/emits and used to derive lanes and permissions.【F:backend/rpc/models.py†L49】【F:frontend/assets/rpc-client.js†L952-L990】 |
| `op` | `str` | Operation qualifier (e.g., `call`, `event`).【F:backend/rpc/models.py†L50】【F:frontend/assets/rpc-client.js†L952-L988】 |
| `path` | `str` | Additional routing hint used by handlers to select the operation variant.【F:backend/rpc/models.py†L51】 |
| `args` | `list` | Positional arguments for capability calls initiated by the client.【F:backend/rpc/models.py†L52】【F:frontend/assets/rpc-client.js†L952-L970】 |
| `seq` | `int` | Per-lane delivery sequence number maintained by the sender for ordering.【F:backend/rpc/models.py†L53】【F:frontend/assets/rpc-client.js†L1085-L1190】 |
| `origin` | `dict` | Metadata (e.g., tracing) passed through without affecting permissions.【F:backend/rpc/models.py†L54】 |
| `chunkNo` | `int` | Index of the chunk when streaming payloads.【F:backend/rpc/models.py†L55】 |
| `final` | `int`/`bool` | Marks the final chunk in a stream.【F:backend/rpc/models.py†L56】 |
| `payload` | `dict` | Application data. Helpers enforce payload shapes (e.g., replies require a payload).【F:backend/rpc/models.py†L57】【F:backend/rpc/messages.py†L71-L121】 |

### Helper constructors

Backend helper functions construct validated envelopes while enforcing required
fields, correlating IDs, and copying idempotency keys from the triggering
message.【F:backend/rpc/messages.py†L33-L121】  The frontend mirrors these helpers in
`RpcClient`, guaranteeing symmetric message shapes during creation of hello,
request, emit, subscribe, cancel, unsubscribe, reply, stateUpdate, error, and
ack messages.【F:frontend/assets/rpc-client.js†L873-L1200】  Both sides rely on the
shared defaults for schema version, system lane, and acknowledgment budget.

### Serialization and logging

`sendRPCMessage` serializes models via `safeJsonDumps`, which dumps the Pydantic
model using deterministic JSON separators before transmitting it over the
WebSocket and handing the payload to the RPC logging filter.【F:backend/rpc/transport.py†L28-L43】【F:backend/core/jsonutils.py†L18-L45】  The frontend performs the
reverse operation: incoming JSON is parsed to plain objects and optionally logged
for debugging.【F:frontend/assets/rpc-client.js†L474-L505】【F:frontend/assets/rpc-client.js†L767-L784】
