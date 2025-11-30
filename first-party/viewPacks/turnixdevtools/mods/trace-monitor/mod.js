// first-party/viewPacks/turnixdevtools/mods/trace-monitor/mod.js
const layout = await turnixImport('./core/layout.js');

/**
 * @typedef {Object} TraceEvent
 * @property {string} id
 * @property {string} name
 * @property {string} level
 * @property {string} [spanId]
 * @property {string} [traceId]
 * @property {number} [ts]
 * @property {Record<string, unknown>} [attrs]
 */

export async function onLoad(ctx) {
    const contents = document.getElementById('turnix-contents');
    if(!contents) {
        console.error('[trace-monitor] #turnix-contents not found.');
        return;
    }

    // Ensure layout class is up to date
    try {
        layout.updateNoSidesClass();
    } catch {/* Ignored */}

    const root = document.createElement('section');
    root.className = 'trace-monitor-root';
    root.setAttribute('aria-label', 'Turnix DevTools trace monitor');

    const header = document.createElement('header');
    header.className = 'trace-monitor-header';
    const title = document.createElement('h1');
    title.textContent = 'Turnix DevTools - Trace Monitor';
    const summary = document.createElement('p');
    summary.className = 'trace-monitor-summary';
    summary.textContent = 'Live tracer events from the backend.';
    header.append(title, summary);

    const toolbar = document.createElement('div');
    toolbar.className = 'trace-monitor-toolbar';

    const levelFilter = document.createElement('select');
    levelFilter.className = 'trace-monitor-filter';
    levelFilter.setAttribute('aria-label', 'Filter by level');
    for(const [val, label] of [
        ['', 'All levels'],
        ['error', 'Error'],
        ['warning', 'Warning'],
        ['info', 'Info'],
        ['debug', 'Debug'],
        ['trace', 'Trace'],
    ]) {
        const option = document.createElement('option');
        option.value = val;
        option.textContent = label;
        levelFilter.append(option);
    }

    const btnClear = document.createElement('button');
    btnClear.type = 'button';
    btnClear.className = 'trace-monitor-button';
    btnClear.textContent = 'Clear';

    toolbar.append(levelFilter, btnClear);

    const tableWrapper = document.createElement('div');
    tableWrapper.className = 'trace-monitor-table-wrapper';

    const table = document.createElement('table');
    table.className = 'trace-monitor-table';
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    for(const text of ['Time', 'Level', 'Name', 'Span', 'Trace', 'Details']) {
        const th = document.createElement('th');
        th.textContent = text;
        headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    const tbody = document.createElement('tbody');

    table.append(thead, tbody);
    tableWrapper.appendChild(table);

    root.append(header, toolbar, tableWrapper);
    contents.appendChild(root);

    /** @type {TraceEvent[]} */
    const buffer = [];
    const maxRows = 500;

    const renderRow = (ev) => {
        const tr = document.createElement('tr');
        tr.dataset.level = ev.level || '';

        const iso = typeof ev.ts === 'number' ? new Date(ev.ts).toISOString() : '';
        const tdTime = document.createElement('td');
        tdTime.textContent = iso;

        const tdLevel = document.createElement('td');
        tdLevel.textContent = ev.level || '';
        
        const tdName = document.createElement('td');
        tdName.textContent = ev.name || '';

        const tdSpan = document.createElement('td');
        tdSpan.textContent = ev.spanId || '';

        const tdTrace = document.createElement('td');
        tdTrace.textContent = ev.traceId || '';

        const tdDetails = document.createElement('td');
        if(ev.attrs && Object.keys(ev.attrs).length > 0) {
            const details = document.createElement('details');
            const summary = document.createElement('summary');
            summary.textContent = 'attrs';
            const pre = document.createElement('pre');
            try {
                pre.textContent = JSON.stringify(ev.attrs, null, 2);
            } catch {
                pre.textContent = String(ev.attrs);
            }
            details.append(summary, pre);
            tdDetails.appendChild(details);
        }

        tr.append(tdTime, tdLevel, tdName, tdSpan, tdTrace, tdDetails);
        return tr;
    };

    const applyFilter = () => {
        const wanted = levelFilter.value;
        for(const row of tbody.rows) {
            const rowLevel = row.dataset.level || '';
            row.style.display = !wanted || rowLevel === wanted ? '' : 'none';
        }
    };

    levelFilter.addEventListener('change', applyFilter);
    btnClear.addEventListener('click', () => {
        buffer.length = 0;
        while(tbody.firstChild) tbody.removeChild(tbody.firstChild);
    });

    let sub = null;
    try {
        sub = await ctx.rpc.subscribe(
            {capability: 'trace.stream@1'},
            'events',
            {},                             // No filters for now
            {class: 'request.medium'},
        );
        sub.on('update', (payload) => {
            const events = Array.isArray(payload?.events) ? payload.events : [];
            if(!events.length) return;
            for(const ev of events) {
                buffer.push(ev);
                const row = renderRow(ev);
                tbody.appendChild(row);
            }
            while(buffer.length > maxRows && tbody.firstChild) {
                buffer.shift();
                tbody.removeChild(tbody.firstChild);
            }
            applyFilter();
            tableWrapper.scrollTop = tableWrapper.scrollHeight;
        });
        sub.on('error', (err) => {
            console.error('[trace-monitor] subscription error:', err);
        });
    } catch(err) {
        console.warn('[trace-monitor] Failed to subscribe to trace.stream@1:', err);
    }

    addEventListener('beforeunload', () => {
        try { sub?.close?.(); }
        catch {/* Ignore */}
    });
}
