// frontend/mods/examples/mod.js

let activeChatSub = null;

const Toast = {
    info:  ({text, ttlMs}) => showToast(text, ttlMs, 'info'),
    warn:  ({text, ttlMs}) => showToast(text, ttlMs, 'warn'),
    error: ({text, ttlMs}) => showToast(text, ttlMs, 'error'),
    success: ({text, ttlMs}) => showToast(text, ttlMs, 'success'),
};

// ---------- Toast helper ----------
function showToast(text, ms = 2000, level='info') {
    // Reuse if possible
    let toasts = document.querySelector('.toasts');
    if(!toasts) {
        toasts = document.createElement('div');
        toasts.className = 'toasts';
        // Announce to assistive tech
        toasts.setAttribute('role', 'status');
        toasts.setAttribute('aria-live', 'polite');
        document.body.appendChild(toasts);
    }

    const toast = document.createElement('div');
    toast.className = `toast ${level}`;
    toast.textContent = String(text);
    toasts.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => toast.classList.add('show'));

    const hide = () => {
        toast.classList.remove('show');
        toast.addEventListener('transitionend', () => {
                toast.remove();
                if(toasts.isConnected && toasts.childElementCount === 0) toasts.remove();
        }, { once: true });
    };

    // Auto hide and click to dismiss
    const timer = setTimeout(hide, ms);
    toast.addEventListener('click', () => {
        clearTimeout(timer);
        hide();
    });
}

function appendMessage(role, text, chatEl) {
    const el = document.createElement('div');
    el.className = `msg ${role}`;
    el.textContent = text;
    chatEl.appendChild(el);
    chatEl.scrollTop = chatEl.scrollHeight;
    return el;
}

export async function onLoad(ctx) {
    const appEl = document.querySelector('.contents');

    // Chat area
    const chatEl = document.createElement('div');
    chatEl.id = 'chat';
    chatEl.className = 'chat';
    chatEl.setAttribute('role', 'log');
    chatEl.setAttribute('aria-live', 'polite');
    chatEl.setAttribute('aria-relevant', 'additions text');

    // Composer
    const composerEl = document.createElement('div');
    composerEl.className = 'composer';

    const textareaEl = document.createElement('textarea');
    textareaEl.id = 'input';
    textareaEl.placeholder = 'Type a message...';

    const btnSend = document.createElement('button');
    btnSend.textContent = 'Send';

    // Keep scroll pinned to bottom only if user is already near bottom
    const isAtBottom = () => {
        const slack = 48; // Tolerance before auto-stick in px.
        return chatEl.scrollTop + chatEl.clientHeight >= chatEl.scrollHeight - slack;
    };

    const autoscroll = () => { chatEl.scrollTop = chatEl.scrollHeight; };

    const startStream = async (text) => {
        // Cancel any previous stream
        if(activeChatSub) {
            try { ctx.rpc.unsubscribe(activeChatSub.id); } catch {/* ignored */}
            activeChatSub = null;
        }

        // Render assistant bubble that we'll chunk-update
        const assistantEl = appendMessage('assistant', '', chatEl);

        let buffer = '';
        let raf = null;

        btnSend.disabled = true;
        try {
            const sub = await ctx.rpc.subscribe(
                {capability: 'chat@1'},
                'stream', // path; not used
                'none', // op; not used
                {role: 'user', text: text} // opts → becomes payload on subscribe
            );
            activeChatSub = sub;

            sub.on('update', (payload) => {
                // Payload: {delta?: string, done?: boolean, text?: string}
                if(!payload) return;
                if(typeof payload.delta === 'string') {
                    buffer += payload.delta;
                    // Throttle DOM writes to animation frames
                    if(!raf) {
                        const stick = isAtBottom();
                        raf = requestAnimationFrame(() => {
                            assistantEl.textContent = buffer;
                            if(stick) autoscroll();
                            raf = null;
                        });
                    }
                }
                if(typeof payload.done === 'boolean' && payload.done) {
                    // Prefer authoritative full text
                    if(typeof payload.text === 'string') {
                        buffer = payload.text;
                        assistantEl.textContent = buffer;
                        autoscroll();
                    }
                    // Cleanup
                    try { ctx.rpc.unsubscribe(sub.id); } catch {/* ignored */}
                    if(activeChatSub && activeChatSub.id === sub.id) activeChatSub = null;
                    btnSend.disabled = false;
                }
            });
            sub.on('error', (payload) => {
                const msg = payload?.message ?? 'Unexpected issue occurred';
                assistantEl.textContent = `Error: ${msg}`;
                Toast.error({text: `Chat failed: ${msg}`, ttlMs: 3000});
                btnSend.disabled = false;
                // TODO: subscription might be still fine, or error is fatal - think of how to report it...
            });
        } catch(err) {
            const msg = err?.message ?? String(err);
            assistantEl.textContent = `Error: ${msg}`;
            Toast.error({text: `Chat failed: ${msg}`, ttlMs: 3000});
            btnSend.disabled = false;
            // TODO: subscription might be still fine, or error is fatal - think of how to report it...
        }
    };

    const send = async () => {
        const hasText = typeof textareaEl.value === 'string' && textareaEl.value.trim().length !== 0;
        const text = hasText ? textareaEl.value : 'Hi! I\'m UI, user didn\'t provide any message to send you... Introduce yourself as game master, ready to play a game with user. End it with a joke! Thank you, UI ends.';
        
        // Render user bubble immediately
        appendMessage('user', text, chatEl);
        textareaEl.value = '';
        textareaEl.focus();

        await startStream(text);
    };

    btnSend.onclick = send;

    // Enter = send; Shift+Enter = newline
    textareaEl.addEventListener('keydown', (ev) => {
        if(ev.key === 'Enter' && !ev.shiftKey) {
            ev.preventDefault();
            btnSend.click();
        }
    });

    // Show a toast in the browser when backend asks
    await ctx.rpc.expose('ui.toast@1', {
        call: async(_path, args) => {
            console.log('ui.toast@1 - call is called!');
            const [text = 'Hello from backend!', ms = 1500, level = 'info'] = args || [];
            showToast(text, ms, level);
            return {ok: true};
        }
    });

    // Subscribe to get latest time!
    ctx.rpc.expose('time.service@1', {
        subscribe: async(_path, _opts, ctx2) => {
            // Push time until cancelled
            let timer = setInterval(() => ctx2.push({ now: Date.now() }), 2500);
            ctx2.signal.addEventListener('abort', () => clearInterval(timer));
            return {initial: { now: Date.now() }, onCancel: () => clearInterval(timer)};
        }
    });

    addEventListener('beforeunload', () => {
        try { if(activeChatSub) ctx.rpc.unsubscribe(activeChatSub.id); } catch {/* ignored */}
        activeChatSub = null;
    });

    // reset.layout@1 - Reset panel sizes to stylesheet defaults
    await ctx.rpc.expose('reset.layout@1', {
        call: async(_path, _args) => {
            const sizes = resetLayoutSizes();
            Toast.info({text: 'Layout reset to defaults', ttlMs: 1500});
            return {ok: true, sizes};
        }
    });

    // reset.chat@1 - Clear current chat log
    await ctx.rpc.expose('reset.chat@1', {
        call: async(_path, _args) => {
            const res = resetChatLog(chatEl);
            Toast.info({text: 'Chat cleared', ttlMs: 1200 });
            return {ok: true, ...res};
        }
    });

    composerEl.append(textareaEl, btnSend);
    appEl.append(chatEl, composerEl);

    // Enable draggable panel resizers
    initResizers();
    // Auto-collapse empty panels
    initAutoCollapsePanels();
}

function resetChatLog(chatEl) {
    // Keep the node (and its ARIA props), just clear children
    while(chatEl.firstChild) chatEl.removeChild(chatEl.firstChild);
    return {cleared: true};
}

// ----- Layout resizers ----------------------------------------------------------------------------
const LAYOUT_VARS = {
    left:  { css: '--left-w',   min: 200, max: 560, step: 8, storage: 'layout.leftW' },
    right: { css: '--right-w',  min: 240, max: 560, step: 8, storage: 'layout.rightW' },
    top:   { css: '--top-h',    min: 40,  max: 120, step: 4, storage: 'layout.topH' },
    bottom:{ css: '--bottom-h', min: 0,   max: 240, step: 8, storage: 'layout.bottomH' },
};

const rootStyle = () => getComputedStyle(document.documentElement);

function readVar(cssVar) {
    const val = rootStyle().getPropertyValue(cssVar).trim();
    // Convert px -> number; fallback 0 if empty
    return parseFloat(val || '0');
}

function writeVar(cssVar, px) {
    document.documentElement.style.setProperty(cssVar, `${Math.round(px)}px`);
}

function loadPersisted(cssVar, key) {
    const saved = localStorage.getItem(key);
    if(saved) writeVar(cssVar, parseFloat(saved));
}

function persist(cssVar, key) {
    const val = readVar(cssVar);
    localStorage.setItem(key, String(val));
}

function clamp(num, min, max) {
    return Math.max(min, Math.min(max, num));
}

function addHandle(panelEl, side) {
    // Side: 'left'|'right'|'top'|'bottom'
    const cfg = LAYOUT_VARS[side];
    if(!cfg) return;

    const handle = document.createElement('div');
    handle.className = `resizer ${
        side === 'left' ? 'right' :
            side === 'right' ? 'left' :
                side === 'top' ? 'bottom' : 'top'
    }`;
    handle.tabIndex = 0;
    handle.setAttribute('role', 'separator');
    handle.setAttribute('aria-orientation', side === 'top' || side === 'bottom' ? 'horizontal' : 'vertical');
    handle.setAttribute('aria-label', `Resize ${side} panel`);
    panelEl.appendChild(handle);

    let startPx = 0;
    let startX = 0;
    let startY = 0;
    let dragging = false;
    let prevUserSelect = '';

    function startDrag(ev) {
        dragging = true;
        startPx = readVar(cfg.css);
        startX = ev.clientX;
        startY = ev.clientY;
        prevUserSelect = document.body.style.userSelect;
        document.body.style.userSelect = 'none';
        handle.setPointerCapture(ev.pointerId);
    }

    function move(ev) {
        if(!dragging) return;
        let delta, next;
        if(side === 'left')  { delta = ev.clientX - startX; next = startPx + delta; }
        if(side === 'right') { delta = startX - ev.clientX; next = startPx + delta; }
        if(side === 'top')   { delta = ev.clientY - startY; next = startPx + delta; }
        if(side === 'bottom'){ delta = startY - ev.clientY; next = startPx + delta; }
        next = clamp(next, cfg.min, cfg.max);
        writeVar(cfg.css, next);
    }

    function endDrag(ev) {
        if(!dragging) return;
        dragging = false;
        try { handle.releasePointerCapture(ev.pointerId); } catch {/* ignored */}
        document.body.style.userSelect = prevUserSelect;
        persist(cfg.css, cfg.storage);
    }

    // Pointer events
    handle.addEventListener('pointerdown', (ev) => {
        if(ev.button !== 0) return;
        startDrag(ev);
        // Ensure keyboard arrows work right after dragging begins
        handle.focus();
    });
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);
    handle.addEventListener('lostpointercapture', endDrag);

    // Keyboard resizing
    handle.addEventListener('keydown', (ev) => {
        const accel = ev.shiftKey ? 3 : 1;
        const step = cfg.step * accel;
        let delta = 0;

        if(side === 'left' || side === 'right') {
            if(ev.key === 'ArrowLeft') delta = side === 'left' ? -step : +step;
            if(ev.key === 'ArrowRight') delta = side === 'left' ? +step : -step;
        } else {
            if(ev.key === 'ArrowUp') delta = side === 'top' ? -step : +step;
            if(ev.key === 'ArrowDown') delta = side === 'top' ? +step : -step;
        }
        if(delta !== 0) {
            ev.preventDefault();
            const cur = readVar(cfg.css);
            writeVar(cfg.css, clamp(cur + delta, cfg.min, cfg.max));
            persist(cfg.css, cfg.storage);
        }

        // Home/End: snap to min/max
        if(ev.key === 'Home') {
            ev.preventDefault();
            writeVar(cfg.css, cfg.min);
            persist(cfg.css, cfg.storage);
        }
        if(ev.key === 'End') {
            ev.preventDefault();
            writeVar(cfg.css, cfg.max);
            persist(cfg.css, cfg.storage);
        }
    });

    // Double-click to reset to CSS default (computed from stylesheet)
    const defaultPx = readVar(cfg.css);
    handle.addEventListener('dblclick', () => {
        writeVar(cfg.css, defaultPx);
        persist(cfg.css, cfg.storage);
    });
}

function initResizers() {
    // Restore persisted sizes first
    loadPersisted(LAYOUT_VARS.left.css,   LAYOUT_VARS.left.storage);
    loadPersisted(LAYOUT_VARS.right.css,  LAYOUT_VARS.right.storage);
    loadPersisted(LAYOUT_VARS.top.css,    LAYOUT_VARS.top.storage);
    loadPersisted(LAYOUT_VARS.bottom.css, LAYOUT_VARS.bottom.storage);

    // Add handles only where the panel exists in DOM
    const leftEl   = document.getElementById('turnix-left');
    const rightEl  = document.getElementById('turnix-right');
    const topEl    = document.getElementById('turnix-top');
    const bottomEl = document.getElementById('turnix-bottom');

    if(leftEl)   addHandle(leftEl, 'left');
    if(rightEl)  addHandle(rightEl, 'right');
    if(topEl)    addHandle(topEl, 'top');
    if(bottomEl) addHandle(bottomEl, 'bottom');
}

function resetLayoutSizes() {
    // Remove inline overrides so browser falls back to stylesheet defaults
    for(const {css, storage} of Object.values(LAYOUT_VARS)) {
        document.documentElement.style.removeProperty(css);
        try { localStorage.removeItem(storage); } catch {/* ignored */}
    }
    // Read back effective values after reset (for return payload/logging)
    const cs = getComputedStyle(document.documentElement);
    return {
        leftW:   cs.getPropertyValue('--left-w').trim(),
        rightW:  cs.getPropertyValue('--right-w').trim(),
        topH:    cs.getPropertyValue('--top-h').trim(),
        bottomH: cs.getPropertyValue('--bottom-h').trim(),
    };
}

// ===== Auto-collapse empty panels =====
// Treat panel as "empty" if it has no element children (resizers don't count)
function hasRealContent(panelEl) {
    for(const ch of panelEl.children) {
        if(!ch.classList.contains('resizer')) return true;
    }
    return false;
}

// Read stylesheet defaults once (used when expanding with no persisted value)
function readDefaultSizes() {
    const cs = getComputedStyle(document.documentElement);
    return {
        left:   parseFloat(cs.getPropertyValue('--left-w'))   || 0,
        right:  parseFloat(cs.getPropertyValue('--right-w'))  || 0,
        top:    parseFloat(cs.getPropertyValue('--top-h'))    || 0,
        bottom: parseFloat(cs.getPropertyValue('--bottom-h')) || 0,
    };
}

function getPersistedSize(side) {
    const key = LAYOUT_VARS[side]?.storage;
    if(!key) return null;
    const saved = localStorage.getItem(key);
    if(saved == null) return null;
    const num = parseFloat(saved);
    return Number.isFinite(num) ? num : null;
}

function setSideSize(side, px) {
    const css = LAYOUT_VARS[side]?.css;
    if(!css) return;
    writeVar(css, px);
}

function removeExistingHandle(panelEl) {
    panelEl.querySelectorAll('.resizer').forEach(el => el.remove());
}

function expandPanel(panelEl, side, defaults) {
    // Choose size: persisted → stylesheet default → sensible fallback
    const persisted = getPersistedSize(side);
    const fallback = defaults[side] ?? 0;
    const size = typeof persisted === 'number' ? persisted : fallback;
    setSideSize(side, size);
    panelEl.setAttribute('aria-hidden', 'false');
    // Ensure handle exists
    removeExistingHandle(panelEl);
    addHandle(panelEl, side);
}

function collapsePanel(panelEl, side) {
    // Size to zero, hide from a11y, remove resizer
    setSideSize(side, 0);
    panelEl.setAttribute('aria-hidden', 'true');
    removeExistingHandle(panelEl);
}

function initAutoCollapsePanels() {
    const defaults = readDefaultSizes();

    const map = {
        left:  document.getElementById('turnix-left'),
        right: document.getElementById('turnix-right'),
        top:   document.getElementById('turnix-top'),
        bottom:document.getElementById('turnix-bottom'),
    };

    for(const [side, el] of Object.entries(map)) {
        if(!el) continue;
        if(hasRealContent(el)) {
            expandPanel(el, side, defaults);
        } else {
            collapsePanel(el, side);
        }
    }

    // Observe DOM changes within each panel to toggle collapse/expand
    const obs = new MutationObserver((muts) => {
        for(const mut of muts) {
            const el = mut.target;
            let side = null;
            if(el === map.left) side = 'left';
            else if(el === map.right) side = 'right';
            else if(el === map.top) side = 'top';
            else if(el === map.bottom) side = 'bottom';
            if(!side) continue;
            // If panel gained content, expand it; if it lost all content, collapse it
            if(hasRealContent(el)) expandPanel(el, side, defaults);
            else collapsePanel(el, side);
            updateNoSidesClass();
        }
    });

    for(const el of Object.values(map)) {
        if(!el) continue;
        obs.observe(el, {childList: true, subtree: false});
    }

    updateNoSidesClass();
}

function updateNoSidesClass() {
    const container = document.querySelector('.container');
    if(!container) return;
    const leftW = readVar('--left-w');
    const rightW = readVar('--right-w');
    const noSides = (leftW === 0 && rightW === 0);
    container.classList.toggle('--no-sides', noSides);
}
