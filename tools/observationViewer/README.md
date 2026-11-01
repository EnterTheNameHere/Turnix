# Independent observation viewer (G-02)

A standalone, read-only browser client for the accepted
`actant.observation-publication@1` contract. It imports no Actant runtime, activates
no Packs and creates no observation sessions. There is no build step, CDN, package
installation, discovery or permanent frontend. Use a modern browser with ES modules,
BigInt, Fetch streams and SVG. Python serves only six fixed public assets.

## Launch and connect

From the repository root, using this checkout's Python environment:

```powershell
.\python-embedded\python.exe -X frozen_modules=off tools/observationViewer/server.py --port 7777
```

1. Open **http://127.0.0.1:7777**. Keep this process running.
2. Before activating `observation.bridge` in the publishing application, configure
   `{"observationBridge":{"port":0,"allowedOrigins":["http://127.0.0.1:7777"]}}`.
   Changing the viewer port requires changing the exact allowed origin before
   activation. `localhost`, file pages and other origins are different origins.
3. The application explicitly supplies the private `observation.bridge.connection@1`
   result and selected `observation.bridge.publish@1` result described in
   [the bridge README](../../first-party/observationBridge/README.md). Paste the
   endpoint, selected publication ID (or its matching snapshot URL) and read
   credential into the three inputs. Click **Connect publication**.

No sample application or consumer branch is modified. Application-owned observation
and publication must already be configured explicitly. Activation alone does not
monitor inference. Use the bridge's ephemeral port or another non-default port;
port 80 is rejected because browsers omit its explicit port from Host/origin
serialization, whereas the accepted G-01 Host contract requires the explicit port.
This viewer does not relax G-01 authorization or route around CORS.

The credential travels only in `Authorization: Bearer …`. It is cleared from the
input on submit, held in this tab's module memory, and discarded on disconnect or
page exit. Requests omit cookies, redirects and referrers. The viewer writes no
browser storage, service workers, files, recording or logs. It disables form
autocomplete and serves no-store assets. User-installed extensions, password
managers and browser developer tools remain outside this client’s control.

**Pause** aborts/suspends only this tab's polling and retains its current evidence
and credential in memory for **Resume**. **Disconnect** clears them. Neither action
releases a source session, withdraws a publication, cancels inference or affects
another viewer. Closing the static server also has no application teardown role.

## Graph and failure semantics

All five plots share one host-process monotonic timeline and cursor. Select all
retained evidence or the last 10/30/60 seconds. Move over a plot or focus a mark to
inspect exact nanoseconds, values, collection spans, target/actual cadence, delivery
gaps and uncovered hardware intervals. Wall time appears separately for display.

- Text throughput is delivered **Unicode characters/second**, never inferred tokens.
  A measured zero-rate window has a baseline mark; unavailable windows are hatched.
- CPU and GPU use percent; RAM and VRAM use GiB (2^30 bytes). Inspection retains exact
  source byte counts. Hardware is **whole-host**, not attributed to the application.
- Hardware bars represent sequential collection intervals. Blank space has no retained
  collection; no lines interpolate between reads. Unknown timing has no invented
  plot position. Hatched intervals mean unavailable sensors, independently of zero.
- Retention warnings show exact dropped stream windows and conservative hardware
  truncation. Older snapshots are replaced, never accumulated into local history.
- Repeated successful HTTP reads do not imply fresh hardware. “Stale hardware” means
  its newest collection interval has been unchanged since this viewer first saw it
  for at least max(5 seconds, 3 target cadences). Earlier age is unknown. This uses
  browser monotonic elapsed time, never a browser/server clock comparison or wall time.
  A stopped collection is labelled final evidence.
- 401/403/404/410 and invalid schema stop polling and clear plotted evidence. Network
  failures and 503 show explicit unavailable/disconnected state and may retain the
  last valid historical snapshot while polling continues at a bounded cadence.
  Browsers hide CORS rejection details: an unapproved origin can appear as a generic
  connection failure. Check the exact pre-activation allowlist; do not weaken it.
- A clock epoch, session or publication identity change clears prior associations.
  No clocks or independent publications are joined. Wall-clock jumps affect display
  only. Input/schema failures use fixed messages without reflecting response bodies.

## Small module interfaces and bounds

`model.mjs` has no HTTP/DOM dependency: `parseSnapshot(raw)` validates v1 into a
frozen allowlisted model; `secondsFrom(ns, origin)` subtracts BigInt before Number;
`timeRange(model, lastSeconds)` and `graphSeries(model, kind)` produce relative
ranges/interval marks; `ObservationStore` replaces its latest model and tracks
identity, revision and unchanged-hardware freshness. Decimal evidence is parsed
losslessly as BigInt (up to 80 decimal digits), with at most 512 hardware samples,
512 stream windows and bounded correlation/per-core input. Unknown metadata is
discarded. Exact values stay available for inspection; plotted values are approximate.

`polling.mjs` separates `connectionConfig`, `readPublication` and injectable `Poller`
from the model/presentation. Default polling is one second after completion, with a
five-second abort deadline, maximum 2 MiB response, at least 250 ms cadence and one
outstanding request even across reconnects. Generation checks discard late results.
Reconnect waits for old work to settle rather than overlapping requests. If an
injected transport ignores abort forever, no further request starts. There is no
source-mutation request or general HTTP proxy. Each viewer owns its own transport.

## Verification

Node.js 20+ runs deterministic tests; set `ACTANT_NODE` to its executable if it is
not on PATH. Node and Playwright are development tools only. Python's suite wrapper
fails with an actionable message if Node is missing; it does not silently skip.

```powershell
node --test tests/tools/observationViewer/viewer.test.mjs
.\python-embedded\python.exe -X frozen_modules=off -m pytest -q --import-mode=importlib tests/tools/observationViewer
.\python-embedded\python.exe -X frozen_modules=off -m pytest -q --import-mode=importlib tests
```

The explicit browser harness uses a real local G-01 Pack/HTTP endpoint, ephemeral
ports, synthetic accepted telemetry/stream observations and two independent browser
pages. Endpoint and credential reach its driver over a private stdin pipe, never
command arguments or files. It verifies source claims/read counts, credential
handling, pause/resume, stale data, 401, 410, 404 and disconnect. The test-only owner
revokes/withdraws through in-process controls, with no new HTTP mutation endpoint.

```powershell
.\python-embedded\python.exe -X frozen_modules=off tests/tools/observationViewer/smoke.py --playwright <absolute-playwright-module-directory> --browser <absolute-browser-executable> --output tools/observationViewer/verification
```

[Verification evidence](verification/README.md) records the actual environment,
commands, results and inspected screenshots. Synthetic observations establish no
real GPU, inference-server, consumer application or other-browser validation.
