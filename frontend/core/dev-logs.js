// frontend/core/dev-logs.js
const { createEmitter } = await turnixImport('./core/rpc-client.js');

/**
 * DevLogs: receives backend "system.logs" emits and fans them out to listeners.
 * Each emit payload looks like: { entries: [jsonLineString, ...] }
 * 
 * Usage:
 *   const logs = registerDevLogs(rpc, { ui: true, maxEntries: 500 });
 *   const sub = logs.onLog((entry) => console.log(entry));
 *     later:
 *   sub.unsubscribe()
 */

export function registerDevLogs(rpc, {ui = true, maxEntries = 500} = {}) {
    const onLog = createEmitter();   // Emits single parsed log entries
    const onBatch = createEmitter(); // Emits raw batch arrays

    // Ensure we only expose once per rpc instance (hot reload friendly)
    const FLAG = Symbol.for('turnix.devLogs.exposed');
    if(!rpc[FLAG]) {
        rpc.expose('system.logs', {
            /**
             * @param {string} _path
             * @param {(entries?: string[])} payload
             */
            emit(_path, payload) {
                const entries = Array.isArray(payload?.entries) ? payload.entries : [];
                if(entries.length === 0) return;

                onBatch.emit(entries);

                for(const line of entries) {
                    const obj = tryParseJson(line);
                    if(obj) onLog.emit(obj);
                    else onLog.emit({level: 'info', logger: 'system.logs', msg: String(line)});
                }
            },
        });
        rpc[FLAG] = true;
    }

    const uiCtrl = ui ? attachInlineLogPane(onLog, {maxEntries}) : null;

    return {
        /**
         * Listen to individual parsed entries.
         * @param {(entry: any) => void} fn
         * @returns {{unsubscribe(): void, fn:(entry:any) => void}}
         */
        onLog: (fn) => onLog.add(fn),
        /**
         * Listen to raw string batches.
         * @param {(entries: string[]) => void} fn
         * @returns {{unsubscribe(): void, fn:(entries:string[]) => void}}
         */
        onBatch: (fn) => onBatch.add(fn),
        destroy() {
            uiCtrl?.destroy();
        }
    };
}

function tryParseJson(line) {
    if(typeof line !== 'string') return null;
    const str = line.trimStart();
    if(!str) return null;
    const first = str[0];
    // Accept object or array JSON
    if(first === '{' || first === '[') {
        try { return JSON.parse(str); }
        catch { return null; }
    }
    return null;
}

function attachInlineLogPane(onLog, {maxEntries}) {
    // Create a floating, collapsible pane bottom-left
    const elRoot = document.createElement('div');
    elRoot.className = 'turnix-log-pane';
    
    const elHeader = document.createElement('div');
    elHeader.className = 'header';
    elHeader.innerHTML = '<strong>Turnix Logs</strong><span>system.logs</span>';
   
    const elButtons = document.createElement('div');
    elButtons.className = 'buttons';
    const elBtnPause = makeButton('', 'pause');       // Label handled by CSS
    const elBtnClear = makeButton('Clear', 'clear');
    const elBtnCollapse = makeButton('', 'collapse'); // Icon handled by CSS

    elButtons.append(elBtnPause, elBtnClear, elBtnCollapse);
    elHeader.append(elButtons);

    const elBody = document.createElement('div');
    elBody.className = 'body';
    const rootState = {paused: false, collapsed: false};

    elBtnPause.onclick = () => {
        rootState.paused = !rootState.paused;
        elRoot.classList.toggle('paused', rootState.paused);
    };
    elBtnClear.onclick = () => {
        elBody.innerHTML = '';
    };
    elBtnCollapse.onclick = () => {
        rootState.collapsed = !rootState.collapsed;
        elRoot.classList.toggle('collapsed', rootState.collapsed);
    };

    const unsub = onLog.add((entry) => {
        if(rootState.paused) return;
        const el = renderEntry(entry);
        elBody.appendChild(el);

        // Trim to maxEntries
        const max = Math.max(50, Number(maxEntries) || 500);
        while(elBody.childElementCount > max) elBody.firstElementChild?.remove();
        
        // Auto-scroll stick to bottom
        const stick = elBody.scrollTop + elBody.clientHeight >= elBody.scrollHeight - 48;
        if(stick) elBody.scrollTop = elBody.scrollHeight;
    });

    elRoot.append(elHeader, elBody);
    document.body.appendChild(elRoot);

    return {
        destroy() {
            try { unsub.unsubscribe(); } catch {/* ignore */}
            try { elBody.remove(); } catch {/* ignore */}
        },
    };
}

function makeButton(text, cssClass) {
    const button = document.createElement('button');
    button.className = `log-button ${cssClass || ''}`.trim();
    if(text) button.textContent = text; // Most buttons text rely on CSS
    return button;
}

function renderEntry(entry) {
    const line = document.createElement('div');
    line.className = 'entry';

    // Try structured format {level, source|logger, message|msg, ...}
    if(entry && typeof entry === 'object') {
        const level = String(entry.level ?? entry.severity ?? '').toLowerCase();
        line.classList.add(`level-${level || 'info'}`);
        
        const logger = entry.source ?? entry.logger ?? 'app';
        const msg = entry.message ?? entry.msg ?? '';
        const ts = typeof entry.ts === 'number'
            ? entry.ts
            : (typeof entry.timestamp === 'string' ? Date.parse(entry.timestamp) : null);
        
        const tag = `[${logger}]${level ? ' ' + level.toUpperCase() : ''}`;
        const time = Number.isFinite(ts) ? ` ${new Date(ts).toISOString()}` : '';
        
        const extras = {...entry};
        delete extras.level;
        delete extras.severity;
        delete extras.source;
        delete extras.logger;
        delete extras.message;
        delete extras.msg;
        delete extras.ts;
        delete extras.timestamp;

        line.textContent = `${tag}${time}: ${String(msg)}`;

        if(Object.keys(extras).length) {
            const details = document.createElement('details');
            const summary = document.createElement('summary');
            summary.textContent = 'details';
            const pre = document.createElement('pre');
            pre.textContent = safeJson(extras);
            details.append(summary, pre);
            line.appendChild(details);
        }
        return line;
    }

    // Fallback to plain string
    line.textContent = String(entry);
    return line;
}

function safeJson(obj) {
    try { return JSON.stringify(obj, null, 2); }
    catch { return String(obj); }
}
