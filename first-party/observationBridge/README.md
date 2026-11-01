# Optional Observation Bridge (G-01)

Pack ID: `observation.bridge`, version `0.1.0`, kind `modPack`.
Add `first-party/observationBridge` to the existing PackResolver roots and include
`observation.bridge` explicitly in the publishing application's ManualActivationPlan.
Importing the package starts nothing. Activation creates one IPv4 loopback endpoint;
it creates no observation session and never attaches to inference automatically.

Example application configuration:

```json
{"observationBridge": {"port": 0, "allowedOrigins": ["http://127.0.0.1:7777"]}}
```

`port=0` chooses an ephemeral available port. The only accepted bind host is
`127.0.0.1`. Omit `allowedOrigins` for Origin-free authenticated local tools only.
Browser origins must be explicitly configured exact local HTTP origins, including
their port. Wildcards, `null` (including file pages), paths and external origins are
rejected. G-02, the standalone browser viewer, is a later task.

## Application contract

Within a valid invocation in the publishing ApplicationRun:

```python
from backend.observation import ObservationLifetime, ObservationRequest

session = ctx.observations.request(
    ObservationRequest(historySamples=300, historyWindows=120),
    lifetime=ObservationLifetime.APPLICATION,
)
grant = ctx.observations.export(session)
publication = ctx.capabilities.call("observation.bridge.publish@1", {"grant": grant})
connection = ctx.capabilities.call("observation.bridge.connection@1")
# An explicitly observed call may use ctx.llm.run(..., observation=session).
# Keep connection private; pass it explicitly to the intended reader, never log it.
```

`export(session)` validates the actual same-ApplicationRun owner. Raw sessions and
payload application IDs are not accepted as publication authority. The grant's
public interface is `snapshot()`, `revoke()` and `revoked`. Its frozen snapshot
contains opaque `sessionId`, `clockEpochId`, source lifetime/request, and the existing
immutable observation evidence. Grants retain no invocation context or callback,
acquire no collection or source-lifetime claim, and provide no execution, source
release, subscription creation or discovery API. In-process Python Packs remain
trusted code; this object API is not a Python introspection sandbox.

The bridge's fresh publication invocation calls `ctx.observations.bindReadGrant()`
to validate ownership and derive its own independently revocable access. Multiple
publications can use one exported parent grant. Withdrawing one derived reader
leaves the parent, other publications, inference, and source session intact.
Revoking the parent ends dependent reading. Source session release or application/
host owner teardown revokes every source grant. A RUN source is still released on
invocation invalidation: exporting it never grants a longer RUN lifetime.

`observation.bridge.publish@1` takes exactly `{grant}` and returns `publicationId`,
`snapshotUrl`, `schemaId`, `sessionId`, `clockEpochId`. It rejects expired, foreign or
invalid authority. The registry holds at most eight selected publications. Source
retention must be at most 512 hardware samples and 512 stream windows; larger
requests are rejected, not silently truncated. The existing source retention and
cadence remain under application ownership. Each publication caches only its latest
bounded response, with a maximum encoded size of 2 MiB. Per-core CPU output is capped
at 256 entries with an explicit `droppedCoreCount`. No persistent history exists.

`observation.bridge.withdraw@1` takes exactly `{publicationId}` and returns whether
it removed an entry. It is idempotent and removes that entry's cached evidence and
read access. Revoked entries otherwise remain bounded 410 stubs until withdrawn.

`observation.bridge.connection@1` takes no payload and returns `endpoint`,
`readCredential`, `authorization="Bearer"`, `schemaId` and `allowedOrigins`.
This is an explicit same-application capability, not an HTTP discovery route.
The application/user supplies this private descriptor plus the selected publication
URL out of band to G-02 (for example, copy/paste into its connection form). The later
viewer should keep the credential only in memory and use an Authorization header.
Do not put it in query strings, shareable URLs, logs or browser storage by default.
There is no G-02 viewer or credential-entry UI in this Pack.

## Replaceable HTTP polling adapter

Only `GET /publications/<opaque-id>` reads observations. Every request needs
`Authorization: Bearer <readCredential>`; the random 256-bit credential is generated
per activated endpoint. Multiple readers share the same grant/collection. HTTP
reads never acquire a collector or invoke runtime capabilities.

Host must equal `127.0.0.1:<actual-port>` exactly. An Origin header, when present,
must match the configured allowlist exactly; an absent Origin still requires the
credential. Browser OPTIONS preflight validates Host, Origin, requested GET method
and requested Authorization header, then returns no observation data. There are no
cookies, wildcard CORS, credentials in URLs, external bindings, redirects or access
logs. Successful/error replies use `Cache-Control: no-store`. All HTTP connections
close after one response. The adapter has one request worker, backlog eight, finite
socket I/O timeouts, and actively interrupts accepted sockets during shutdown.

Read status: 200 snapshot, 401 credential rejected, 403 Host/Origin rejected, 400
invalid target (including queries/listing), 404 unknown/withdrawn publication, 410
source/parent grant revoked, 503 optional observation failure or shutdown. There is
no cached fallback on 410/503. Mutation methods are unsupported. No listing or other
application data API exists.

Withdrawal serializes against response sending and returns after any preceding
send has completed. Source-grant reads linearize under the observation service lock;
an already copied response may finish after source revocation, but later reads fail.
Every HTTP read revalidates authority before consulting cached output. Cached bytes
can remain bounded in memory until that next validation/withdrawal/unload; they are
never served after a failed validation. On unload the Pack rejects new work,
interrupts accepted sockets, withdraws readers/caches, and closes/joins the endpoint.
Failed activation self-cleans even when PackLoader can supply only None unload state.
The existing PM limitation on a hung native read remains: revocation/read/shutdown
can wait for a source service lock held by hardware; no interruption mechanism was
added. The bridge is optional observation and does not own inference outcomes.

## Transport-independent JSON v1

`schemaId = "actant.observation-publication@1"`. `schema.py:serialize()` is independent
of HTTP. A later viewer should reject other schema versions and reset association
when the clock epoch changes. No prompts, response text, arbitrary provider metadata,
raw error messages, exception objects, tracebacks, Python object dictionaries, GPU
labels/performance-state strings or unrelated application identity/data are encoded.
Native engine tokens remain in the original engine result and are not exposed here.

All exact integer evidence is a **decimal JSON string**: nanoseconds, counts, byte
amounts, revisions, indices representing device identity, and retention limits.
Unknown timing/counts are JSON null. `hardwareIndices` in correlation are small JSON
array indices, bounded by 512. Percentages, seconds and delivery rates are finite JSON
numbers; nonfinite/unrepresentable scalar values are explicitly unavailable. For
graph coordinates, parse nanoseconds as BigInt, subtract the common origin, then
convert relative deltas to plotting numbers. Never compare wall time to monotonic
time or concatenate different epochs.

Top-level contract:

| Field | Meaning |
| --- | --- |
| `publicationId`, `sessionId`, `revision` | Opaque selected identities; revision changes only when serialized evidence changes. It is not a new hardware collection counter. |
| `clock.kind`, `clock.epochId` | `host-process-monotonic`; random ObservationService composition epoch, shared by its application owners. Reconstructed services get a new epoch, including injected clock domains. |
| `encodings`, `units`, `scope` | Integer/rate encoding; nanoseconds, Unicode code points, delivery events; whole-host hardware plus application-selected stream. |
| `sourceLifetime`, `needs` | Source RUN/APPLICATION lifetime and requested signal groups. |
| `retention` | Configured sample/window bounds, conservative `hardwareMayBeTruncated`; `hardwareDroppedCount=null` because PM-01 has no eviction counter. |
| `sampleIntervalSeconds`, `windowSeconds` | Requested cadence/window length, not a promise of exact polling. |
| `hardwareAvailability`, `streamAvailability`, `collectionStopped` | Fixed facility markers and stopped collection evidence; per-signal availability remains separate. |
| `hardware` | Ordered bounded samples. Original collection start/end Ns, separate display Unix wall Ns, negotiated target and actual start-spacing seconds. Read intervals contain sequential sensors, never simultaneous measurements. |
| `stream` | Null if no stream evidence; otherwise fixed availability/state, actual provider-span outcome, active/released, origin/observed-through/end/first/last text Ns, counts, first-text latency, silence, exact `droppedWindowCount`, bounded windows. A completed stream does not imply Job success. |
| `correlation` | Per stream window: start/end Ns, overlapping `hardwareIndices`, and `uncoveredIntervalsNs`. These expose lack of retained collection activity, not proof that polls were lost. No interpolation is implied. |

Each graphable hardware/rate scalar has `{availability, unit, value}`. Availability
is `available` or `unavailable`; unavailable value is null, never a fabricated zero.
Hardware groups include physical memory/swap bytes and percent, aggregate/per-core
CPU percent and topology counts, GPU/VRAM bytes/utilization, and optional numerical
power/clock/temperature/fan sensors. Raw unavailable reasons are deliberately omitted.

Stream window fields retain exact start/end Ns, character/delivery counts, first/last
text Ns, gap count/sum/min/max Ns, partial flag and tagged characters/second. Exact
mean gap is derivable from gap sum/count. Empty completed windows mean observed
silence (available zero rate); missing hardware or an unavailable stream is a different
state. Zero-duration rates are unavailable. Delivery characters are Unicode code
points, never tokens. Wall-clock jumps do not change ordering, rates or alignment.
