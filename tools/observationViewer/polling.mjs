// file: tools/observationViewer/polling.mjs ; version: 1
/** Read-only transport and generation-safe polling, independent of graphs and DOM. */
import {parseSnapshot, SchemaError} from './model.mjs';
export const MAX_BODY = 2 * 1024 * 1024;

/** Fixed public connection failure with no URL, credential or server body attached. */
export class ConnectionError extends Error {
  constructor(code) { super(code); this.code = code; }
}
/** Accept only an explicit G-01 literal-loopback endpoint and selected opaque publication. */
export function connectionConfig(endpoint, publication, credential) {
  try {
    const url = new URL(endpoint);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || (url.pathname !== '/' && url.pathname !== '') || url.search || url.hash || url.username || url.password) throw new Error();
    let selected = publication;
    if (publication.startsWith('http')) {
      const target = new URL(publication);
      if (target.origin !== url.origin || target.search || target.hash || target.username || target.password) throw new Error();
      selected = target.pathname.replace(/^\/publications\//, '');
    }
    if (!/^[0-9a-f]{32}$/.test(selected) || !/^[A-Za-z0-9_-]{16,256}$/.test(credential)) throw new Error();
    return Object.freeze({url: url.origin + '/publications/' + selected, publicationId: selected, credential});
  } catch { throw new ConnectionError('invalid-connection'); }
}
/** Read a response incrementally up to the accepted bridge limit; no unbounded text(). */
async function boundedJson(response) {
  if (!response.headers.get('content-type')?.toLowerCase().startsWith('application/json')) { await response.body?.cancel(); throw new SchemaError(); }
  const length = response.headers.get('content-length');
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > MAX_BODY)) { await response.body?.cancel(); throw new SchemaError(); }
  if (!response.body) throw new SchemaError();
  const reader = response.body.getReader(); const chunks = []; let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY) { await reader.cancel(); throw new SchemaError(); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    try { return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes)); } catch { throw new SchemaError(); }
  } finally { reader.releaseLock(); }
}
/** Fetch one authorized snapshot only; no discovery, source mutation or raw error propagation. */
export async function readPublication(config, signal, fetcher = globalThis.fetch) {
  const response = await fetcher(config.url, {method: 'GET', headers: {Authorization: 'Bearer ' + config.credential},
    signal, credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer'});
  if (!response.ok) {
    await response.body?.cancel();
    throw new ConnectionError(({401: 'unauthorized', 403: 'forbidden', 404: 'withdrawn', 410: 'revoked', 503: 'unavailable'})[response.status] ?? 'disconnected');
  }
  const model = parseSnapshot(await boundedJson(response));
  if (model.publicationId !== config.publicationId) throw new SchemaError();
  return model;
}

/** One outstanding request including across reconnects; old responses are never accepted. */
export class Poller {
  constructor({onModel, onState, read = readPublication, schedule = (callback, delay) => globalThis.setTimeout(callback, delay), cancel = id => globalThis.clearTimeout(id), interval = 1000, timeout = 5000}) {
    this.onModel = onModel; this.onState = onState; this.read = read; this.schedule = schedule; this.cancel = cancel;
    this.interval = Math.max(250, interval); this.timeout = Math.max(250, timeout);
    this.generation = 0; this.pending = false; this.running = false; this.config = null; this.timer = null; this.controller = null;
  }
  /** Start an explicitly supplied connection, cancelling old work before replacing credentials. */
  connect(config) { this.stopWork(); this.config = config; this.running = true; this.onState('connecting'); this.tick(); }
  /** Stop just this viewer's polling and retain its in-memory connection/evidence for resume. */
  pause() { this.stopWork(); this.onState('paused'); }
  /** Resume only with an existing explicit connection; no source subscription is acquired. */
  resume() { if (!this.config || this.running) return; this.running = true; this.onState('connecting'); this.tick(); }
  /** Clear credentials and cancel local transport only; no bridge/source mutation. */
  disconnect() { this.stopWork(); this.config = null; this.onState('disconnected'); }
  /** Invalidate both pending and scheduled generations without starting another concurrent request. */
  stopWork() {
    this.running = false; this.generation++; this.controller?.abort();
    if (this.timer !== null) this.cancel(this.timer); this.timer = null;
  }
  /** Complete or settle aborted work before scheduling exactly one subsequent request. */
  async tick() {
    if (this.pending || !this.running || !this.config) return;
    const generation = this.generation, config = this.config;
    this.pending = true; this.controller = new AbortController();
    const timeout = this.schedule(() => this.controller?.abort(), this.timeout);
    try {
      const model = await this.read(config, this.controller.signal);
      if (generation === this.generation && this.running) { this.onModel(model); this.onState('connected'); }
    } catch (error) {
      if (generation === this.generation && this.running) {
        const code = error instanceof SchemaError ? 'invalid-schema' : error instanceof ConnectionError ? error.code : 'disconnected';
        if (['unauthorized', 'forbidden', 'withdrawn', 'revoked', 'invalid-schema'].includes(code)) this.running = false;
        this.onState(code);
      }
    } finally {
      this.cancel(timeout); this.pending = false; this.controller = null;
      if (this.running && this.config) this.timer = this.schedule(() => { this.timer = null; this.tick(); }, generation === this.generation ? this.interval : 0);
    }
  }
}
