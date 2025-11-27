// frontend/core/layout.js
// Shared layout + panel sizing utilities for Turnix views.

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
    try {
        const saved = localStorage.getItem(key);
        if(saved) writeVar(cssVar, parseFloat(saved));
    } catch {
        // Storage might be disabled. Ignore.
    }
}

function persist(cssVar, key) {
    const val = readVar(cssVar);
    try {
        localStorage.setItem(key, String(val));
    } catch {
        // Storage might be disabled. Ignore.
    }
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
        let delta;
        let next;
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

export function initResizers() {
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

export function resetLayoutSizes() {
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

// ----- Auto-collapse empty panels -----
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
    try {
        const saved = localStorage.getItem(key);
        if(saved == null) return null;
        const num = parseFloat(saved);
        return Number.isFinite(num) ? num : null;
    } catch {
        return null;
    }
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

function updateNoSidesClass() {
    const container = document.querySelector('.container');
    if(!container) return;
    const cs = getComputedStyle(document.documentElement);
    const leftW = parseFloat(cs.getPropertyValue('--left-w')) || 0;
    const rightW = parseFloat(cs.getPropertyValue('--right-w')) || 0;
    const noSides = (leftW === 0 && rightW === 0);
    container.classList.toggle('--no-sides', noSides);
}

export function initAutoCollapsePanels() {
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
