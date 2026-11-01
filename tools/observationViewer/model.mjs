// file: tools/observationViewer/model.mjs ; version: 1
/** Pure bounded v1 parsing and time-aligned graph model. No HTTP, DOM or persistence. */
export const SCHEMA = 'actant.observation-publication@1';
export const LIMIT = 512;

/** Fixed, non-reflective schema failure; never embeds incoming data. */
export class SchemaError extends Error {
  constructor() { super('Unsupported or malformed observation snapshot.'); this.code = 'invalid-schema'; }
}
/** Assert a schema invariant without reflecting its input. */
function check(ok) { if (!ok) throw new SchemaError(); }
/** Require an ordinary JSON record. */
function object(v) { check(v !== null && typeof v === 'object' && !Array.isArray(v)); return v; }
/** Read exact bounded decimal evidence, with signed display wall time only when requested. */
function integer(v, nullable = true, signed = false) {
  if (v === null && nullable) return null;
  check(typeof v === 'string' && (signed ? /^-?(0|[1-9]\d{0,79})$/ : /^(0|[1-9]\d{0,79})$/).test(v));
  return BigInt(v);
}
/** Require a boolean rather than truthiness. */
function boolean(v) { check(typeof v === 'boolean'); return v; }
/** Require a finite nonnegative number or explicitly unknown interval. */
function number(v, nullable = true) { if (v === null && nullable) return null; check(typeof v === 'number' && Number.isFinite(v) && v >= 0); return v; }
/** Validate an opaque G-01 identifier. */
function id(v) { check(typeof v === 'string' && /^[0-9a-f]{32}$/.test(v)); return v; }
/** Bound arrays before mapping them into retained model state. */
function array(v, max = LIMIT) { check(Array.isArray(v) && v.length <= max); return v; }
/** Parse an explicitly tagged graph scalar, retaining exact byte amounts. */
function scalar(v, unit, integral = false) {
  object(v); check(v.unit === unit && ['available', 'unavailable'].includes(v.availability));
  if (v.availability === 'unavailable') { check(v.value === null); return null; }
  return integral ? integer(v.value, false) : number(v.value, false);
}
/** Validate an ordered time span, retaining unknown hardware timing without a fabricated position. */
function span(start, end) {
  const a = integer(start), b = integer(end);
  check(a === null || b === null || a <= b);
  return [a, b];
}
/** Parse only selected schema evidence; unknown fields/provider strings are discarded. */
export function parseSnapshot(raw) {
  object(raw); check(raw.schemaId === SCHEMA);
  const publicationId = id(raw.publicationId), sessionId = id(raw.sessionId), revision = integer(raw.revision, false);
  check(revision > 0n); object(raw.clock);
  check(raw.clock.kind === 'host-process-monotonic' && raw.clock.nanosecondsEncoding === 'decimal-string');
  const epochId = id(raw.clock.epochId);
  check(object(raw.encodings).integerEvidence === 'decimal-string');
  check(object(raw.units).timestampsAndGaps === 'nanoseconds' && raw.units.streamCharacters === 'unicode-code-points');
  check(raw.scope === 'whole-host-hardware/application-selected-stream');
  check(['run', 'application'].includes(raw.sourceLifetime));
  const needs = Object.fromEntries(['memory', 'cpu', 'gpu', 'stream'].map(k => [k, boolean(object(raw.needs)[k])]));
  const retention = object(raw.retention);
  const hardwareLimit = integer(retention.hardwareSamples, false), windowLimit = integer(retention.streamWindows, false);
  check(hardwareLimit > 0n && hardwareLimit <= 512n && windowLimit > 0n && windowLimit <= 512n && retention.hardwareDroppedCount === null);
  const truncatedHardware = boolean(retention.hardwareMayBeTruncated);
  check(['unavailable', 'as-reported-per-signal'].includes(raw.hardwareAvailability));
  check(['unavailable', 'disabled', 'as-reported'].includes(raw.streamAvailability));
  const sampleIntervalSeconds = number(raw.sampleIntervalSeconds, false), windowSeconds = number(raw.windowSeconds, false);
  check(sampleIntervalSeconds > 0 && windowSeconds > 0);
  const hardware = array(raw.hardware).map(sample => {
    object(sample); const [start, end] = span(sample.collectionStartNs, sample.collectionEndNs);
    const cpu = object(sample.cpu), gpu = object(sample.gpu), memory = object(sample.memory);
    array(cpu.perLogicalCorePercent, 256); // Even discarded input arrays remain bounded.
    return Object.freeze({start, end, wall: integer(sample.displayWallTimeNs, true, true),
      targetSeconds: number(sample.targetIntervalSeconds), actualSeconds: number(sample.actualIntervalSeconds),
      cpu: scalar(cpu.aggregatePercent, 'percent'), gpu: scalar(gpu.gpuUtilizationPercent, 'percent'),
      ram: scalar(memory.usedBytes, 'bytes', true), ramTotal: scalar(memory.totalBytes, 'bytes', true),
      vram: scalar(gpu.usedVramBytes, 'bytes', true), vramTotal: scalar(gpu.totalVramBytes, 'bytes', true),
      droppedCores: integer(cpu.droppedCoreCount, false)});
  });
  check(hardware.length <= Number(hardwareLimit));
  let previous = null;
  for (const sample of hardware) { if (sample.start !== null) { check(previous === null || previous <= sample.start); previous = sample.start; } }
  let stream = null;
  if (raw.stream !== null) {
    const s = object(raw.stream);
    check(['available', 'unavailable'].includes(s.availability) && ['unavailable', 'not-started', 'active', 'terminal'].includes(s.state));
    check([null, 'completed', 'failed', 'cancelled', 'closed'].includes(s.outcome));
    const origin = integer(s.originNs), through = integer(s.observedThroughNs), end = integer(s.endNs);
    check(origin === null || through === null || through >= origin); check(end === null || origin === null || end >= origin);
    const windows = array(s.windows).map(w => {
      object(w); const [start, end] = span(w.startNs, w.endNs); check(start !== null && end !== null);
      const chars = integer(w.characterCount, false), deliveries = integer(w.deliveryCount, false);
      const rate = scalar(w.charactersPerSecond, 'unicode-code-points/second');
      check(start !== end || rate === null);
      return Object.freeze({start, end, chars, deliveries, rate,
        first: integer(w.firstTextNs), last: integer(w.lastTextNs), partial: boolean(w.partial),
        gapCount: integer(w.gapCount, false), gapSum: integer(w.gapSumNs, false), minGap: integer(w.minGapNs), maxGap: integer(w.maxGapNs)});
    });
    check(windows.length <= Number(windowLimit));
    for (let i = 1; i < windows.length; i++) check(windows[i - 1].end <= windows[i].start);
    stream = Object.freeze({origin, through, end, first: integer(s.firstTextNs), last: integer(s.lastTextNs),
      latency: integer(s.firstTextLatencyNs), silence: integer(s.silenceNs), count: integer(s.characterCount, false),
      deliveries: integer(s.deliveryCount, false), dropped: integer(s.droppedWindowCount, false),
      active: boolean(s.active), released: boolean(s.released), state: s.state, outcome: s.outcome,
      available: s.availability === 'available' && raw.streamAvailability !== 'unavailable', windows: Object.freeze(windows)});
  }
  const correlation = array(raw.correlation).map((row, i) => {
    object(row); const [start, end] = span(row.startNs, row.endNs);
    check(stream !== null && i < stream.windows.length && start === stream.windows[i].start && end === stream.windows[i].end);
    const indices = array(row.hardwareIndices).map(index => { check(Number.isInteger(index) && index >= 0 && index < hardware.length); return index; });
    check(new Set(indices).size === indices.length);
    const gaps = array(row.uncoveredIntervalsNs, LIMIT + 1).map(pair => {
      check(Array.isArray(pair) && pair.length === 2); const [a, b] = span(...pair);
      check(a !== null && b !== null && start <= a && a < b && b <= end); return Object.freeze([a, b]);
    });
    return Object.freeze({start, end, indices: Object.freeze(indices), gaps: Object.freeze(gaps)});
  });
  check(correlation.length === (stream?.windows.length ?? 0));
  return Object.freeze({publicationId, sessionId, epochId, revision, sourceLifetime: raw.sourceLifetime,
    needs: Object.freeze(needs), sampleIntervalSeconds, windowSeconds, truncatedHardware,
    hardwareUnavailable: raw.hardwareAvailability === 'unavailable', stopped: boolean(raw.collectionStopped),
    hardware: Object.freeze(hardware), stream, correlation: Object.freeze(correlation)});
}

/** Subtract exact nanoseconds before converting relative coordinates to floating point. */
export function secondsFrom(ns, origin) { return Number(ns - origin) / 1e9; }
/** Produce shared-domain ranges; no wall time participates in placement or rates. */
export function timeRange(model, lastSeconds = null) {
  const starts = model.hardware.filter(s => s.start !== null && s.end !== null).map(s => s.start);
  const ends = model.hardware.filter(s => s.start !== null && s.end !== null).map(s => s.end);
  for (const w of model.stream?.windows ?? []) { starts.push(w.start); ends.push(w.end); }
  const origin = model.stream?.origin ?? starts[0] ?? 0n;
  let start = starts.reduce((a, b) => a < b ? a : b, starts[0] ?? origin), end = ends.reduce((a, b) => a > b ? a : b, ends[0] ?? origin);
  if (end === start) end += 1_000_000_000n;
  if (lastSeconds !== null) { const lower = end - BigInt(lastSeconds) * 1_000_000_000n; if (lower > start) start = lower; }
  return {origin, start, end, left: secondsFrom(start, origin), right: secondsFrom(end, origin)};
}
/** Build interval marks, never a line connecting separated hardware reads. */
export function graphSeries(model, kind) {
  if (kind === 'stream') return (model.stream?.windows ?? []).map(w => ({...w, value: model.stream.available ? w.rate : null}));
  return model.hardware.filter(s => s.start !== null && s.end !== null).map(s => ({...s,
    value: model.hardwareUnavailable || s[kind] === null ? null : ['ram', 'vram'].includes(kind) ? Number(s[kind]) / 2**30 : s[kind]}));
}

/** One latest normalized snapshot and client-monotonic freshness markers; no accumulated history. */
export class ObservationStore {
  constructor() { this.reset(); }
  /** Clears prior identity/epoch and selection association on explicit disconnect/reconnect. */
  reset() { this.model = null; this.hardwareStamp = null; this.hardwareSeen = null; this.received = null; this.changedEpoch = false; }
  /** Atomically replace evidence; reject revision regression and never join clock epochs. */
  apply(model, now) {
    const old = this.model;
    const changed = old !== null && (old.epochId !== model.epochId || old.sessionId !== model.sessionId || old.publicationId !== model.publicationId);
    if (changed) this.reset();
    if (!changed && old !== null && model.revision < old.revision) throw new SchemaError();
    this.changedEpoch = changed;
    const stamp = model.hardware.reduce((value, s) => s.end !== null && (value === null || s.end > value) ? s.end : value, null);
    if (this.model === null || stamp !== this.hardwareStamp) this.hardwareSeen = now;
    this.hardwareStamp = stamp; this.received = now; this.model = model;
    return changed;
  }
  /** Freshness is time unchanged since this viewer saw evidence, never absolute server age. */
  freshness(now) {
    const m = this.model;
    if (m === null) return {state: 'empty', unchangedSeconds: 0};
    const elapsed = Math.max(0, (now - this.hardwareSeen) / 1000);
    if (m.stopped) return {state: 'final', unchangedSeconds: elapsed};
    const needed = m.needs.cpu || m.needs.gpu || m.needs.memory;
    return {state: needed && elapsed >= Math.max(5, 3 * m.sampleIntervalSeconds) ? 'stale' : 'current', unchangedSeconds: elapsed};
  }
}
