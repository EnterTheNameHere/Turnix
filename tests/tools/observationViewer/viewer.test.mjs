// file: tests/tools/observationViewer/viewer.test.mjs ; version: 1
/** Deterministic parser/model/transport/polling tests with no browser or native measurements. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseSnapshot, SchemaError, secondsFrom, timeRange, graphSeries, ObservationStore} from '../../../tools/observationViewer/model.mjs';
import {connectionConfig, readPublication, Poller, MAX_BODY} from '../../../tools/observationViewer/polling.mjs';
import {fixture, PUB, BASE} from './fixtures.mjs';

/** Flush async test continuations without advancing real or fake wall clocks. */
async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
/** Manual finite scheduler supports exact polling bounds and timeout assertions. */
class Scheduler {
  constructor() { this.now = 0; this.next = 0; this.tasks = new Map(); }
  add = (callback, delay) => { const id = ++this.next; this.tasks.set(id, {callback, at: this.now + delay}); return id; };
  cancel = id => this.tasks.delete(id);
  async advance(ms) {
    const until = this.now + ms; let count = 0;
    while (true) {
      const next = [...this.tasks].filter(([, task]) => task.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      assert.ok(++count < 100, 'scheduler spin'); this.now = next[1].at; this.tasks.delete(next[0]); next[1].callback(); await flush();
    }
    this.now = until; await flush();
  }
}
/** A private deferred read can intentionally ignore abort to test reconnect ordering. */
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; }
/** Build one independent test viewer with deterministic scheduler and request observations. */
function harness(read) {
  const clock = new Scheduler(), models = [], states = [];
  const poller = new Poller({read, schedule: clock.add, cancel: clock.cancel, onModel: m => models.push(m), onState: s => states.push(s)});
  return {clock, models, states, poller};
}
const config = connectionConfig('http://127.0.0.1:49152', PUB, 'test-only-credential');

test('availability differs from numeric zero and observed silence', () => {
  const m = parseSnapshot(fixture());
  assert.equal(m.hardware[0].cpu, 0); assert.equal(m.hardware[0].gpu, null);
  assert.equal(m.hardware[0].vram, 0n); assert.equal(m.stream.windows[0].rate, 0);
  assert.equal(graphSeries(m, 'gpu')[0].value, null); assert.equal(graphSeries(m, 'stream')[0].value, 0);
  const raw = fixture(); raw.stream.availability = 'unavailable'; assert.equal(graphSeries(parseSnapshot(raw), 'stream')[0].value, null);
});
test('exact timestamps and bytes survive before relative floating point conversion', () => {
  const m = parseSnapshot(fixture()); assert.equal(m.hardware[0].start, BASE + 5n); assert.equal(m.hardware[0].ram, 2n ** 55n + 1n);
  assert.equal(secondsFrom(BASE + 5n, BASE), 5e-9); assert.equal(timeRange(m).origin, BASE);
  assert.equal(m.hardware[0].wall, 2n ** 61n + 77n);
});
test('delayed collection intervals, uncovered gaps and truncation stay explicit', () => {
  const m = parseSnapshot(fixture()); assert.equal(m.hardware[0].end - m.hardware[0].start, 100n);
  assert.equal(m.correlation[0].gaps.length, 2); assert.equal(m.truncatedHardware, true); assert.equal(m.stream.dropped, 4n);
  assert.equal(graphSeries(m, 'cpu').length, 1); // No invented connecting segment.
});
test('wall clock jumps cannot affect ordering, rates or graph coordinates', () => {
  const raw = fixture(), before = parseSnapshot(raw); raw.hardware[0].displayWallTimeNs = '-123';
  const after = parseSnapshot(raw); assert.deepEqual(timeRange(after), timeRange(before)); assert.deepEqual(graphSeries(after, 'stream'), graphSeries(before, 'stream'));
  assert.equal(after.hardware[0].wall, -123n);
});
test('unknown timing has no fabricated placement', () => {
  const raw = fixture(); raw.hardware[0].collectionStartNs = raw.hardware[0].collectionEndNs = null; raw.correlation[0].hardwareIndices = [];
  assert.equal(graphSeries(parseSnapshot(raw), 'cpu').length, 0);
});
for (const [name, mutate] of [
  ['version', r => r.schemaId = 'actant.observation-publication@2'], ['clock', r => r.clock.kind = 'wall'],
  ['timestamp number', r => r.hardware[0].collectionStartNs = 2**60], ['invalid decimal', r => r.stream.originNs = '1.2'],
  ['regressing interval', r => r.hardware[0].collectionEndNs = '0'], ['wrong unit', r => r.stream.windows[0].charactersPerSecond.unit = 'tokens/second'],
  ['oversized history', r => r.hardware = Array(513).fill(r.hardware[0])], ['bad availability', r => r.hardware[0].gpu.gpuUtilizationPercent.value = 0],
  ['missing field', r => delete r.retention], ['invalid reference', r => r.correlation[0].hardwareIndices = [9]],
  ['nonfinite percent', r => r.hardware[0].cpu.aggregatePercent.value = Infinity], ['unsafe ID', r => r.sessionId = '<script>'],
]) test('rejects malformed ' + name, () => { const raw = fixture(); mutate(raw); assert.throws(() => parseSnapshot(raw), SchemaError); });
test('unknown private fields are discarded, not retained in the model', () => {
  const raw = fixture(); raw.providerMetadata = {secret: 'not-public'}; raw.prompt = 'private';
  const m = parseSnapshot(raw); assert.equal('providerMetadata' in m, false); assert.equal('prompt' in m, false); assert.equal(Object.isFrozen(m), true);
});
test('epoch/session changes reset association and old revisions are rejected', () => {
  const store = new ObservationStore(), first = parseSnapshot(fixture()); store.apply(first, 0);
  const raw = fixture(); raw.clock.epochId = '4'.repeat(32); raw.revision = '1';
  assert.equal(store.apply(parseSnapshot(raw), 8000), true); assert.equal(store.freshness(8000).state, 'current'); assert.equal(store.model.epochId, raw.clock.epochId);
  raw.revision = '5'; store.apply(parseSnapshot(raw), 9000); raw.revision = '4'; assert.throws(() => store.apply(parseSnapshot(raw), 10000), SchemaError);
});
test('staleness uses client monotonic unchanged time, not response or wall freshness', () => {
  const store = new ObservationStore(); store.apply(parseSnapshot(fixture()), 1000);
  store.apply(parseSnapshot(fixture()), 7000); assert.equal(store.freshness(7000).state, 'stale');
  const raw = fixture(); raw.collectionStopped = true; store.apply(parseSnapshot(raw), 8000); assert.equal(store.freshness(9000).state, 'final');
});
test('latest replacement remains bounded without accumulating historical windows', () => {
  const store = new ObservationStore(); for (let i = 1; i <= 1000; i++) { const raw = fixture(); raw.revision = String(i); store.apply(parseSnapshot(raw), i); }
  assert.equal(store.model.hardware.length, 1); assert.equal(store.model.stream.windows.length, 1); assert.equal('history' in store, false);
});
for (const [name, endpoint, selected] of [
  ['remote', 'http://example.com:8000', PUB], ['default port', 'http://127.0.0.1:80', PUB],
  ['URL credential', 'http://secret@127.0.0.1:8000', PUB], ['query', 'http://127.0.0.1:8000?token=secret', PUB],
  ['foreign publication', 'http://127.0.0.1:8000', 'http://127.0.0.1:8001/publications/' + PUB],
]) test('connection rejects ' + name, () => assert.throws(() => connectionConfig(endpoint, selected, 'test-only-credential')));
test('transport uses Bearer only, no cookies/query/redirects or source mutations', async () => {
  let request;
  const m = await readPublication(config, new AbortController().signal, async (url, options) => { request = {url, options}; return Response.json(fixture()); });
  assert.equal(m.publicationId, PUB); assert.equal(request.options.method, 'GET'); assert.equal(request.options.credentials, 'omit');
  assert.equal(request.options.headers.Authorization, 'Bearer test-only-credential'); assert.equal(request.url.includes('credential'), false);
  assert.equal(request.options.redirect, 'error');
});
for (const [status, code] of [[401, 'unauthorized'], [403, 'forbidden'], [404, 'withdrawn'], [410, 'revoked'], [503, 'unavailable']]) {
  test('HTTP ' + status + ' is a fixed public state without reflected body', async () => {
    await assert.rejects(readPublication(config, new AbortController().signal, async () => new Response('secret raw diagnostic', {status})), error => error.code === code && !error.message.includes('secret'));
  });
}
test('unsupported, oversized and malformed response bodies are bounded schema failures', async () => {
  for (const response of [new Response('bad'), new Response('{}', {headers: {'Content-Type': 'application/json', 'Content-Length': String(MAX_BODY + 1)}}),
    new Response('bad', {headers: {'Content-Type': 'application/json'}})]) {
    await assert.rejects(readPublication(config, new AbortController().signal, async () => response), SchemaError);
  }
});
test('chunked oversized bodies are cancelled without trusting a missing length header', async () => {
  let cancelled = false;
  const body = new ReadableStream({start(controller) {
    controller.enqueue(new Uint8Array(MAX_BODY)); controller.enqueue(new Uint8Array(1));
  }, cancel() { cancelled = true; }});
  await assert.rejects(readPublication(config, new AbortController().signal,
    async () => new Response(body, {headers: {'Content-Type': 'application/json'}})), SchemaError);
  assert.equal(cancelled, true);
});

test('a valid snapshot for an unrelated publication is rejected', async () => {
  const raw = fixture(); raw.publicationId = '9'.repeat(32);
  await assert.rejects(readPublication(config, new AbortController().signal, async () => Response.json(raw)), SchemaError);
});

test('one pending request, no overlap and pause stops polling without mutations', async () => {
  const job = deferred(); let calls = 0;
  const {poller, clock, models} = harness(() => { calls++; return job.promise; });
  poller.connect(config); await clock.advance(2000); assert.equal(calls, 1);
  poller.pause(); job.resolve(parseSnapshot(fixture())); await flush(); await clock.advance(10000);
  assert.equal(calls, 1); assert.equal(models.length, 0); assert.equal(poller.config, config);
  poller.disconnect(); assert.equal(poller.config, null);
});
test('reconnect waits for old abort settlement and ignores its late response', async () => {
  const old = deferred(), fresh = deferred(); let calls = 0;
  const {poller, clock, models} = harness(() => ++calls === 1 ? old.promise : fresh.promise);
  poller.connect(config); poller.connect({...config, credential: 'replacement-credential'});
  assert.equal(calls, 1); old.resolve(parseSnapshot(fixture())); await flush(); await clock.advance(0);
  assert.equal(models.length, 0); assert.equal(calls, 2); fresh.resolve(parseSnapshot(fixture())); await flush();
  assert.equal(models.length, 1); poller.disconnect();
});
test('resume is idempotent and independent viewers never share cancellation', async () => {
  const a = harness(async () => parseSnapshot(fixture())), b = harness(async () => parseSnapshot(fixture()));
  a.poller.connect(config); b.poller.connect(config); await flush(); a.poller.pause(); a.poller.resume(); a.poller.resume(); await flush();
  assert.equal(a.clock.tasks.size, 1); a.poller.disconnect(); await b.clock.advance(1000); assert.equal(b.models.length, 2); b.poller.disconnect();
});
test('timeout aborts one request and retries only after a bounded polling interval', async () => {
  let calls = 0;
  const {poller, clock, states} = harness((_config, signal) => { calls++; return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('secret')))); });
  poller.connect(config); await clock.advance(5000); assert.equal(states.at(-1), 'disconnected'); assert.equal(calls, 1);
  await clock.advance(1000); assert.equal(calls, 2); poller.disconnect(); await flush();
});
test('fatal authentication/schema errors stop automatic retries', async () => {
  const {ConnectionError} = await import('../../../tools/observationViewer/polling.mjs');
  const {poller, clock, states} = harness(async () => { throw new ConnectionError('revoked'); });
  poller.connect(config); await flush(); await clock.advance(10000); assert.equal(poller.running, false); assert.equal(states.at(-1), 'revoked'); poller.disconnect();
});
test('source has no credential persistence, console logging or innerHTML sink', () => {
  for (const name of ['app.mjs', 'polling.mjs', 'model.mjs']) {
    const source = readFileSync(new URL('../../../tools/observationViewer/' + name, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie|console\.|innerHTML|sendBeacon/);
  }
});
