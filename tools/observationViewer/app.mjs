// file: tools/observationViewer/app.mjs ; version: 1
/** Standalone read-only DOM/SVG presentation. Connection state stays in this module's memory. */
import {ObservationStore, timeRange, graphSeries, secondsFrom} from './model.mjs';
import {Poller, connectionConfig} from './polling.mjs';

const $ = id => document.getElementById(id);
const store = new ObservationStore();
const colors = {stream: '#19796b', cpu: '#5683b1', gpu: '#a16c42', ram: '#7565ad', vram: '#568b91'};
const names = {stream: 'Text delivery', cpu: 'CPU', gpu: 'GPU', ram: 'RAM', vram: 'VRAM'};
let state = 'disconnected';
let selectedNs = null;
const messages = {
  connecting: 'Connecting to the explicitly selected publication…',
  connected: 'Authenticated read-only polling. Hardware marks are collection intervals; empty space has no retained collection.',
  paused: 'Polling paused in this tab. Retained evidence is paused; the application and other readers are unaffected.',
  disconnected: 'Disconnected. If evidence remains, it is the last valid snapshot. Check the endpoint, local origin and bridge availability.',
  unauthorized: 'Read credential rejected (401). Enter a valid application-supplied credential and reconnect.',
  forbidden: 'Host or browser Origin rejected (403). Configure this viewer’s exact origin before activating the bridge.',
  withdrawn: 'Publication is unknown or withdrawn (404). Select a publication explicitly supplied by the application.',
  revoked: 'Publication reading was revoked (410). Source ownership has ended or its grant was withdrawn.',
  unavailable: 'Source observation is unavailable (503). Last valid evidence is historical; no measurements are being invented.',
  'invalid-schema': 'Unsupported or malformed snapshot. Polling stopped and graphs cleared.',
  'invalid-connection': 'Use an explicit http://127.0.0.1:<non-default-port>, matching publication and private read credential.',
};
const fatal = ['unauthorized', 'forbidden', 'withdrawn', 'revoked', 'invalid-schema'];

/** Construct SVG nodes without HTML interpretation of any input. */
function svgNode(tag, attributes = {}, text = '') {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  node.textContent = text; return node;
}
/** Format approximate plot quantities while keeping exact bytes/Ns in inspection. */
function quantity(value, kind) {
  if (value === null || value === undefined) return 'Unavailable';
  return value.toLocaleString('en', {maximumFractionDigits: kind === 'stream' ? 1 : 2}) + (kind === 'stream' ? ' char/s' : ['ram', 'vram'].includes(kind) ? ' GiB' : ' %');
}
/** Preserve wall evidence separately; no browser wall time participates in alignment/freshness. */
function displayWall(ns) {
  if (ns === null) return 'unknown';
  const ms = ns / 1_000_000n;
  return ms >= -8_640_000_000_000_000n && ms <= 8_640_000_000_000_000n ? new Date(Number(ms)).toISOString() + ' (' + ns + ' ns)' : ns + ' Unix ns';
}
/** Render one interval chart using the same retained monotonic range as all other plots. */
function drawChart(kind, range) {
  const svg = $(kind + '-chart'); svg.replaceChildren();
  const width = kind === 'stream' ? 1000 : 500, height = kind === 'stream' ? 170 : 160;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const left = 36, right = width - 12, top = 10, bottom = height - 32;
  const series = store.model ? graphSeries(store.model, kind) : [];
  const marks = range ? series.filter(m => m.end >= range.start && m.start <= range.end) : [];
  const maximum = ['cpu', 'gpu'].includes(kind) ? Math.max(100, ...marks.map(m => m.value ?? 0)) : Math.max(1, ...marks.map(m => m.value ?? 0)) * 1.15;
  const defs = svgNode('defs');
  const pattern = svgNode('pattern', {id: `${kind}-unavailable`, patternUnits: 'userSpaceOnUse', width: 6, height: 6});
  pattern.append(svgNode('rect', {width: 6, height: 6, fill: '#f5eee6'}), svgNode('path', {d: 'M0 6 L6 0', stroke: '#d7bfa5', 'stroke-width': 1}));
  defs.append(pattern); svg.append(defs);
  svg.append(svgNode('rect', {x: left, y: top, width: right - left, height: bottom - top, fill: '#f8faf8'}));
  for (let i = 0; i <= 2; i++) {
    const y = bottom - (bottom - top) * i / 2;
    svg.append(svgNode('line', {x1: left, x2: right, y1: y, y2: y, stroke: '#e2e9e3', 'stroke-dasharray': '3 4'}));
    svg.append(svgNode('text', {x: left - 7, y: y + 4, 'text-anchor': 'end'}, (maximum * i / 2).toLocaleString('en', {maximumFractionDigits: maximum < 10 ? 1 : 0})));
  }
  if (range) {
    const span = secondsFrom(range.end, range.start);
    const x = ns => left + secondsFrom(ns, range.start) / span * (right - left);
    for (const mark of marks) {
      const a = x(mark.start < range.start ? range.start : mark.start), b = x(mark.end > range.end ? range.end : mark.end);
      const available = mark.value !== null;
      const y = available ? bottom - mark.value / maximum * (bottom - top) : top;
      const rect = svgNode('rect', {x: Math.min(a, right - 2), y: available ? Math.min(y, bottom - 2) : top,
        width: Math.max(2, b - a), height: available ? Math.max(2, bottom - y) : bottom - top,
        fill: available ? (mark.value === 0 ? '#edf6ef' : colors[kind]) : `url(#${kind}-unavailable)`,
        opacity: kind === 'stream' ? 0.82 : 0.66, stroke: available ? colors[kind] : '#d0b89b', 'stroke-width': .6,
        class: 'mark', tabindex: 0, 'aria-label': `${names[kind]} ${quantity(mark.value, kind)}; exact interval ${mark.start} to ${mark.end} nanoseconds`});
      /** Select actual interval evidence rather than interpolating a measurement at the pointer. */
      const inspectMark = () => { selectedNs = (mark.start + mark.end) / 2n; inspect(kind, mark); updateCursors(); };
      rect.addEventListener('pointerenter', inspectMark); rect.addEventListener('focus', inspectMark); rect.addEventListener('click', inspectMark);
      svg.append(rect);
    }
    for (let i = 0; i <= 4; i++) {
      const ns = range.start + (range.end - range.start) * BigInt(i) / 4n;
      svg.append(svgNode('text', {x: left + (right - left) * i / 4, y: height - 10, 'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}, secondsFrom(ns, range.origin).toFixed(1) + ' s'));
    }
    svg.append(svgNode('line', {class: 'cursor', x1: left, x2: left, y1: top, y2: bottom, visibility: 'hidden'}));
    svg.onpointermove = event => {
      const box = svg.getBoundingClientRect(), value = (event.clientX - box.left) / box.width * width;
      const fraction = Math.max(0, Math.min(1, (value - left) / (right - left)));
      selectedNs = range.start + BigInt(Math.round(secondsFrom(range.end, range.start) * 1e9 * fraction));
      inspect(); updateCursors();
    };
  }
  if (!range || !marks.length) {
    const model = store.model;
    const requested = model && (kind === 'stream' ? model.needs.stream : model.needs[kind === 'ram' ? 'memory' : kind === 'vram' ? 'gpu' : kind]);
    const unavailable = model && (kind === 'stream' ? !model.stream?.available : model.hardwareUnavailable);
    const empty = !model ? 'No selected observation' : !requested ? 'Not requested by source' : unavailable ? 'Observation unavailable' : 'No retained observations in this range';
    svg.append(svgNode('text', {x: (left + right) / 2, y: (top + bottom) / 2, 'text-anchor': 'middle'}, empty));
  }
  $(kind + '-value').textContent = series.length ? quantity(series[series.length - 1].value, kind) : '—';
}
/** Draw the same exact selected time across all plots, preserving each chart's own y scale. */
function updateCursors() {
  if (selectedNs === null || !store.model) return;
  const range = currentRange();
  for (const kind of Object.keys(colors)) {
    const width = kind === 'stream' ? 1000 : 500;
    const x = 36 + secondsFrom(selectedNs, range.start) / secondsFrom(range.end, range.start) * (width - 48);
    const cursor = $(kind + '-chart').querySelector('.cursor');
    if (cursor) { cursor.setAttribute('x1', String(x)); cursor.setAttribute('x2', String(x)); cursor.setAttribute('visibility', x >= 36 && x <= width - 12 ? 'visible' : 'hidden'); }
  }
}
/** Return the shared user-selected retained time range. */
function currentRange() { return store.model ? timeRange(store.model, $('range').value === 'all' ? null : Number($('range').value)) : null; }
/** Inspect an exact chosen mark or the nearest recorded interval, explicitly labelling gaps. */
function inspect(kind = null, mark = null) {
  const model = store.model; if (!model || selectedNs === null) return;
  const range = currentRange();
  $('selection').textContent = `${secondsFrom(selectedNs, range.origin).toFixed(3)} s relative to origin`;
  const sample = kind && kind !== 'stream' ? mark : model.hardware.reduce((nearest, s) => {
    if (s.start === null || s.end === null) return nearest;
    const distance = s.start > selectedNs ? s.start - selectedNs : selectedNs > s.end ? selectedNs - s.end : 0n;
    return nearest === null || distance < nearest.distance ? {sample: s, distance} : nearest;
  }, null)?.sample;
  const window = kind === 'stream' ? mark : model.stream?.windows.find(w => w.start <= selectedNs && (selectedNs < w.end || w.start === w.end && w.start === selectedNs));
  const parts = [`Selected monotonic time: ${selectedNs} ns.`];
  if (window) parts.push(`Stream window [${window.start}, ${window.end}) ns · ${window.chars} Unicode characters · ${window.deliveries} deliveries · ${window.rate === null || !model.stream.available ? 'unavailable rate' : window.rate + ' char/s'}${window.chars === 0n && model.stream.available && window.rate === 0 ? ' (observed silence)' : ''}.`);
  else parts.push('No retained stream window at this time.');
  if (window) {
    parts.push(`Inter-delivery gaps: ${window.gapCount}; sum ${window.gapSum} ns; min ${window.minGap ?? 'unknown'} ns; max ${window.maxGap ?? 'unknown'} ns.`);
    const correlation = model.correlation.find(row => row.start === window.start && row.end === window.end);
    if (correlation?.gaps.length) parts.push('No retained hardware coverage in this window: ' + correlation.gaps.map(([a, b]) => `[${a}, ${b}) ns`).join(', ') + '.');
  }
  if (sample) {
    if (model.hardwareUnavailable) parts.push('Hardware facility unavailable; retained raw values are incomplete evidence.');
    const inside = sample.start <= selectedNs && selectedNs <= sample.end;
    parts.push(`${inside ? 'Selected collection interval' : 'No collection at cursor; nearest recorded interval'} [${sample.start}, ${sample.end}] ns. CPU ${quantity(sample.cpu, 'cpu')} · GPU ${quantity(sample.gpu, 'gpu')} · RAM ${sample.ram === null ? 'unavailable' : sample.ram + ' bytes'} · VRAM ${sample.vram === null ? 'unavailable' : sample.vram + ' bytes'}. Target ${sample.targetSeconds ?? 'unknown'} s; actual spacing ${sample.actualSeconds ?? 'unknown'} s. Display wall time: ${displayWall(sample.wall)}.`);
  } else parts.push('Hardware collection timing is unavailable.');
  $('details').textContent = parts.join(' ');
}
/** Render only bounded normalized evidence; no source JSON or credential is attached to the DOM. */
function render() {
  const m = store.model;
  $('source').textContent = m ? `Publication …${m.publicationId.slice(-8)} · ${m.sourceLifetime.toUpperCase()} source` : 'No publication selected';
  $('epoch').textContent = m ? `Clock epoch …${m.epochId.slice(-8)} · evidence revision ${m.revision}` : 'One monotonic clock epoch per view';
  $('latency').textContent = m?.stream?.latency !== null && m?.stream?.latency !== undefined ? (Number(m.stream.latency) / 1e6).toLocaleString('en', {maximumFractionDigits: 2}) + ' ms' : '—';
  $('characters').textContent = m?.stream ? m.stream.count.toLocaleString('en') : '—';
  $('outcome').textContent = m?.stream?.outcome ?? m?.stream?.state ?? '—';
  const notes = [];
  if (m?.truncatedHardware) notes.push('Hardware history may be truncated (exact loss unknown)');
  if (m?.stream?.dropped) notes.push(`${m.stream.dropped} stream windows dropped`);
  const cores = m?.hardware.reduce((v, s) => v > s.droppedCores ? v : s.droppedCores, 0n);
  if (cores) notes.push(`${cores} per-core entries omitted by bridge`);
  const unknown = m?.hardware.filter(s => s.start === null || s.end === null).length;
  if (unknown) notes.push(`${unknown} samples have unknown collection timing`);
  $('retention').textContent = notes.join(' · ') || 'Bounded source snapshots · no local history recording';
  const range = currentRange();
  if (selectedNs !== null && range && (selectedNs < range.start || selectedNs > range.end)) selectedNs = null;
  if (!m || selectedNs === null) {
    $('selection').textContent = 'Move across a plot or focus a measurement';
    $('details').textContent = 'Exact monotonic intervals and separate display wall time appear here.';
  }
  for (const kind of Object.keys(colors)) drawChart(kind, range);
  if (selectedNs !== null) inspect();
  refreshStatus(); updateCursors();
}
/** Present transport state and unchanged-source freshness as different evidence. */
function refreshStatus() {
  const freshness = store.freshness(performance.now());
  const stale = state === 'connected' && freshness.state === 'stale';
  $('status').textContent = stale ? 'Stale hardware' : ({connected: 'Connected', connecting: 'Connecting', paused: 'Paused', disconnected: 'Disconnected', unauthorized: 'Unauthorized', forbidden: 'Origin rejected', withdrawn: 'Withdrawn', revoked: 'Revoked', unavailable: 'Source unavailable', 'invalid-schema': 'Invalid snapshot', 'invalid-connection': 'Check connection'})[state];
  $('status').className = 'status ' + (stale || state === 'paused' ? 'paused' : state === 'connected' ? 'connected' : fatal.includes(state) || state === 'unavailable' || state === 'invalid-connection' ? 'error' : '');
  $('notice').className = 'notice ' + (fatal.includes(state) || state === 'invalid-connection' ? 'error' : '');
  $('notice').textContent = stale ? `Hardware interval unchanged for ${Math.floor(freshness.unchangedSeconds)} s since this viewer first saw it. Earlier age is unknown; this is not fresh hardware evidence.` : (messages[state] ?? messages.disconnected) + (freshness.state === 'final' && state === 'connected' ? ' Collection stopped: final retained evidence.' : '') + (store.changedEpoch ? ' Clock/source identity changed; prior timeline and selection were cleared.' : '');
  $('pause').disabled = !poller.config || fatal.includes(state); $('pause').textContent = state === 'paused' ? 'Resume' : 'Pause';
  $('disconnect').disabled = !poller.config;
}
const poller = new Poller({
  onModel(model) { if (store.apply(model, performance.now())) selectedNs = null; render(); },
  onState(next) { state = next; if (fatal.includes(next)) { store.reset(); selectedNs = null; render(); } else refreshStatus(); },
});
$('origin').textContent = location.origin;
$('connection').addEventListener('submit', event => {
  event.preventDefault(); const credential = $('credential').value; $('credential').value = '';
  try {
    const config = connectionConfig($('endpoint').value.trim(), $('publication').value.trim(), credential);
    store.reset(); selectedNs = null; poller.connect(config); render();
  } catch { poller.disconnect(); store.reset(); state = 'invalid-connection'; render(); }
});
$('pause').addEventListener('click', () => state === 'paused' ? poller.resume() : poller.pause());
$('disconnect').addEventListener('click', () => { poller.disconnect(); store.reset(); selectedNs = null; $('credential').value = ''; render(); });
$('range').addEventListener('change', render);
window.addEventListener('pagehide', () => { poller.disconnect(); store.reset(); selectedNs = null; $('credential').value = ''; render(); });
window.addEventListener('pageshow', () => { $('credential').value = ''; });
setInterval(refreshStatus, 1000);
render();
