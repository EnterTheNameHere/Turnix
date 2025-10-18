# WebSocket RPC Flow

This guide explains how the Turnix runtime wires the browser to the backend over
`/ws`, and how `RPCMessage` envelopes travel across that socket.  It combines
behavior from the FastAPI endpoint, the session manager, and the browser
`RpcClient` implementation.

## HTTP bootstrap before the socket opens

Before the browser touches the WebSocket it performs two HTTP fetches that
hydrate runtime configuration and establish identity:

1. **Settings fetch** – `/settings` returns the merged server configuration.
   The bootstrap script freezes it onto `globalThis.Turnix` so early modules can
   read feature flags and other environment switches before RPC is ready.【F:frontend/bootstrap.js†L37-L88】【F:backend/app/web.py†L13-L18】
2. **View bootstrap** – `/api/bootstrap` issues or refreshes a `clientId`
   cookie, binds it to a view, and returns the `viewId`, `viewToken`, and the
   server's current generation number.  The frontend includes these fields in
   the subsequent `hello` payload along with a per-tab `clientInstanceId` and
   the last persisted generation so reconnects can resume cleanly.【F:frontend/bootstrap.js†L40-L84】【F:backend/api/bootstrap.py†L16-L61】【F:frontend/bootstrap.js†L61-L83】

The generated `viewToken` is cached server-side by `ViewRegistry`, ensuring only
the authenticated client can bind to that view during the WebSocket handshake.
If a browser opens multiple tabs the server will reuse the same view binding for
their shared `clientId` while minting distinct tokens for each tab.【F:backend/views/registry.py†L11-L67】

## Connection lifecycle

1. **WebSocket upgrade** – After bootstrap the browser connects to
   `ws(s)://…/ws`.  The backend registers this endpoint via
   `mountWebSocket`.  After accepting the socket it enters a receive loop that
   filters out non-text frames and guards against oversized payloads before
   attempting to validate them as `RPCMessage` instances.【F:backend/rpc/transport.py†L67-L115】
2. **Client hello** – Immediately after `WebSocket.OPEN`, the frontend sends a
   hello frame containing optional view metadata.  The helper enforces the
   standard schema version, `sys` lane, and placeholder generation.【F:frontend/assets/rpc-client.js†L356-L374】【F:frontend/assets/rpc-client.js†L908-L926】
3. **Welcome** – On the first valid `hello`, the server resolves or creates a
   view, binds the WebSocket to that view, allocates a new generation via
   `RPCSession.newGeneration`, patches view state, and returns a `welcome`
   message that carries the generation and a snapshot of the current state.【F:backend/rpc/transport.py†L120-L160】【F:backend/rpc/session.py†L12-L41】
4. **Client ready** – After the welcome arrives the browser marks the connection
   as ready, flushes queued messages, and resumes subscriptions.  It can then
   report module load status via `clientReady`, which the backend acknowledges
   while recording module metadata and preventing duplicate processing per
   generation.【F:frontend/assets/rpc-client.js†L474-L522】【F:frontend/assets/rpc-client.js†L450-L466】【F:backend/rpc/transport.py†L174-L236】
5. **Heartbeat** – Both sides maintain liveness.  The client periodically sends
   `heartbeat` frames; the server updates its timestamp and ACKs them.  Missed
   heartbeats trigger reconnection logic in the browser.【F:backend/rpc/transport.py†L242-L245】【F:frontend/assets/rpc-client.js†L1099-L1126】
6. **Disconnect** – When the socket closes, outstanding request and subscription
   tasks are cancelled on the server, the view binding is removed, and the socket
   is closed.  The client tears down timers, rejects pending promises, and
   schedules exponential backoff reconnect attempts.【F:backend/rpc/transport.py†L323-L337】【F:frontend/assets/rpc-client.js†L376-L403】

## Session and generation tracking

`RPCSession` tracks per-view/client state: idempotency cache, pending tasks,
subscription coroutines, and generation metadata.  Each successful `hello`
bumps the generation counter and salt, ensuring replayed messages from older
sessions can be ignored.【F:backend/rpc/session.py†L12-L60】  The frontend stores the
most recent generation and discards any message that does not match it.【F:frontend/assets/rpc-client.js†L489-L527】

## Message dispatch

Once the handshake completes, the backend immediately ACKs every non-control
message and dispatches based on the `type` and `route`:

- **Requests** – Routed either to object handlers or capability-based request
  handlers.  The transport checks permissions through `_ensureCapabilityOrError`
  before invoking the registered handler.  Errors are wrapped in `error`
  envelopes tied to the triggering message.【F:backend/rpc/transport.py†L238-L321】【F:backend/rpc/transport.py†L47-L64】【F:backend/rpc/messages.py†L71-L121】
- **Emits and subscribes** – Resolved against capability-specific handlers.
  Subscribes are tracked so later `cancel` / `unsubscribe` messages can stop the
  running task and drop chat subscriptions.【F:backend/rpc/transport.py†L247-L321】
- **Cancels** – Remove pending work and cancel live subscriptions, cleaning up
  session-side tracking structures.【F:backend/rpc/transport.py†L247-L263】

On the browser, incoming workload messages targeting exposed capabilities are
ACKed automatically, then routed to the registered `call`, `emit`, or
`subscribe` handler.  Replies and errors resolve or reject pending promises,
while subscription updates trigger local event emitters.【F:frontend/assets/rpc-client.js†L529-L700】

## Acknowledgements and flow control

- **Automatic ACKs** – The backend ACKs every message other than `ack` and
  `heartbeat`, and the frontend mirrors this rule for all messages except the
  handshake/control set.  Both sides use helper constructors to tie the ACK to
  the original message ID and to place it on the `sys` lane.【F:backend/rpc/transport.py†L238-L245】【F:frontend/assets/rpc-client.js†L645-L824】【F:backend/rpc/messages.py†L51-L67】
- **Budget and retry windows** – Request and emit helpers populate `budgetMs`
  from timeout classes.  The browser keeps per-lane in-flight counters and
  queues additional work until ACKs or replies arrive, preventing overload while
  providing best-effort cancellation on timeout.【F:frontend/assets/rpc-client.js†L952-L999】【F:frontend/assets/rpc-client.js†L703-L765】【F:frontend/assets/rpc-client.js†L1146-L1194】
- **Idempotency** – Client helpers copy an `idempotencyKey` for request/emit
  messages.  The server session caches IDs and previous replies so repeated
  invocations can be deduplicated.【F:frontend/assets/rpc-client.js†L952-L990】【F:backend/rpc/session.py†L17-L60】

## Permissions and principals

Before invoking capability handlers the backend derives the principal from the
message and validates that it has the required capability.  Permission failures
are converted into structured `error` messages containing retry metadata, so the
client can surface clean feedback.【F:backend/rpc/transport.py†L47-L64】

## Logging and observability

Each outbound frame is serialized via `safeJsonDumps` and passed through the RPC
logging decision engine, enabling centrally controlled logging on both sides of
the connection.  The browser offers matching logging hooks that honor the same
filters to keep debugging consistent.【F:backend/rpc/transport.py†L28-L43】【F:frontend/assets/rpc-client.js†L767-L784】
