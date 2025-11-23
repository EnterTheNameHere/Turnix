// first-party/appPacks/main-menu/mods/main-menu-ui/main-menu.js
const STYLE = `
:root {
    color-scheme: dark;
}

.turnix-main-menu {
    font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
    padding: 2rem;
    max-width: 720px;
    margin: 0 auto;
}

.turnix-main-menu h1 {
    font-size: 2.5rem;
    margin: 0;
}

.turnix-main-menu__actions {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
}

.turnix-main-menu button {
    background: #5865f2;
    color: #fff;
    border: none;
    padding: 0.85rem 1.5rem;
    border-radius: 999px;
    font-size: 1rem;
    cursor: pointer;
    transition: background 120ms ease;
}

.turnix-main-menu button.secondary {
    background: rgba(255,255,255,0.08);
}

.turnix-main-menu button:hover {
    background: #4752c4;
}

.turnix-main-menu__subtitle {
    color: rgba(255,255,255,0.65);
    margin: 0;
}

.turnix-mm-dialog {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.65);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 999;
}

.turnix-mm-dialog__panel {
    background: #11131c;
    border-radius: 18px;
    padding: 1.5rem;
    width: min(640px, calc(100% - 2rem));
    display: flex;
    flex-direction: column;
    gap: 1rem;
    box-shadow: 0 40px 80px rgba(0,0,0,0.35);
}

.turnix-mm-dialog__list {
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    overflow: auto;
    max-heigh: 360px;
}

.turnix-mm-dialog__item {
    padding: 0.85rem 1rem;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    cursor: pointer;
}

.turnix-mm-dialog__item:last-child {
    border-bottom: none;
}

.turnix-mm-dialog__item[data-selected="true"] {
    background:  rgba(88,101,242,0.25)
}

.turnix-mm-dialog__actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
}

.turnix-mm-form {
    display: grid;
    gap: 0.75rem;
}

.turnix-mm-form label {
    display: flex;
    flex-directory: column;
    gap: 0.35rem;
    font-size: 0.85rem;
    color: rgba(255,255,255,0.75);
}

.turnix-mm-form input {
    border: 1px solid rgba(255,255,255,0.15);
    background: rgba(255,255,255,0.04);
    border-radius: 999px;
    padding: 0.6rem 1rem;
    color: #fff;
}

.turnix-toast-container {
    position: fixed;
    bottom: 1.5rem;
    right: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    z-index: 1000;
}

.turnix-toast {
    background: rgba(17, 19, 28, 0.9);
    color: #fff;
    padding: 0.75rem 1rem;
    border-radius: 999px;
    min-width: 220px;
    border: 1px solid rgba(255,255,255,0.08);
}
`;

const _APP_PACK_ID = 'Turnix@main-menu';

function ensureStyles() {
    if(document.getElementById('turnix-main-menu-style')) return;
    const style = document.createElement('style');
    style.id = 'turnix-main-menu-style';
    style.textContent = STYLE;
    document.head.appendChild(style);
}

function showToast(message, ttl = 2500) {
    let container = document.querySelector('.turnix-toast-container');
    if(!container) {
        container = document.createElement('div');
        container.className = 'turnix-toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = 'turnix-toast';
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.remove();
        if(container.childElementCount === 0) container.remove();
    }, ttl);
}

function createDialog({title, body, actions = []}) {
    const overlay = document.createElement('div');
    overlay.className = 'turnix-mm-dialog';
    const panel = document.createElement('div');
    panel.className = 'turnix-mm-dialog__panel';

    const heading = document.createElement('h2');
    heading.textContent = title;

    const bodyWrap = document.createElement('div');
    bodyWrap.appendChild(body);

    const footer = document.createElement('div');
    footer.className = 'turnix-mm-dialog__actions';
    actions.forEach(({label, variant = 'primary', onClick}) => {
        const btn = document.createElement('button');
        if(variant === 'secondary') btn.classList.add('secondary');
        btn.textContent = label;
        btn.addEventListener('click', () => onClick?.(overlay));
        footer.appendChild(btn);
    });

    panel.appendChild(heading, bodyWrap, footer);
    overlay.appendChild(panel);
    overlay.addEventListener('click', (ev) => {
        if(ev.target === overlay) overlay.remove();
    });
    document.body.appendChild(overlay);
    return overlay;
}

function buildList(items, renderLine) {
    const wrapper = document.createElement('div');
    wrapper.className = 'turnix-mm-dialog__list';
    items.forEach((item) => {
        const row = document.createElement('div');
        row.className = 'turnix-mm-dialog__item';
        row.dataset.value = item.id;
        row.innerHTML = renderLine(item);
        wrapper.appendChild(row);
    });
    return wrapper;
}

async function fetchAppPacks(ctx) {
    const res = await ctx.rpc.request(
        { route: { capability: 'main.menu@1' }, path: 'listAppPacks', op: 'call', args: [] },
        {},
        { class: 'request.medium' },
    );

    // In case this appPack (Turnix's main-menu) is on the list, remove it...
    if(Array.isArray(res?.appPacks)) {
        res.appPacks = res.appPacks.filter(item => item.id !== _APP_PACK_ID);
    }

    return Array.isArray(res?.appPacks) ? res.appPacks : [];
}

async function fetchSaves(ctx) {
    const res = await ctx.rpc.request(
        { route: { capability: 'main.menu@1' }, path: 'listSaves', op: 'call', args: [] },
        {},
        { class: 'request.medium' },
    );
    return Array.isArray(res?.saves) ? res.saves : [];
}

async function generateRuntime(ctx, payload) {
    return ctx.rpc.request(
        { route: { capability: 'main.menu@1' }, path: 'generateRuntime', op: 'call', args: [payload] },
        {},
        { class: 'request.medium' },
    );
}

async function loadSave(ctx, payload) {
    return ctx.rpc.request(
        { route: { capability: 'main.menu@1' }, path: 'loadSave', op: 'call', args: [payload] },
        {},
        { class: 'request.medium' },
    );
}

function randomRuntimeInstanceId(appPackId) {
    const suffix = Math.random().toString(36).slice(2, 6);
    return `${appPackId.replace(/[^A-Za-z0-9_-]/gu, '-')}-${suffix}`;
}

function renderMenu(root) {
    const container = document.createElement('div');
    container.className = 'turnix-main-menu';
    const title = document.createElement('h1');
    title.textContent = 'Turnix';
    const subtitle = document.createElement('p');
    subtitle.className = 'turnix-main-menu__subtitle';
    subtitle.textContent = 'Prototype launcher for app packs and save packs.';

    const actions = document.createElement('div');
    actions.className = 'turnix-main-menu__actions';

    const buttons = [
        {label: 'New', id: 'new'},
        {label: 'Load', id: 'load'},
        {label: 'Settings', id: 'settings', secondary: true},
        {label: 'Exit', id: 'exit', secondary: true},
    ];

    buttons.forEach(({label, id, secondary}) => {
        const btn = document.createElement('button');
        btn.textContent = label;
        if(secondary) btn.classList.add('secondary');
        btn.dataset.action = id;
        actions.appendChild(btn);
    });

    container.append(title, subtitle, actions);
    root.appendChild(container);
    return container;
}

export async function onLoad(ctx) {
    ensureStyles();
    const target = document.querySelector('.contents') || document.body;
    target.innerHTML = '';
    const menu = renderMenu(target);

    menu.addEventListener('click', async(ev) => {
        const button = ev.target.closest('button[data-action]');
        if(!button) return;
        const action = button.dataset.action;
        try {
            if(action === 'new') await showNewDialog(ctx);
            else if(action === 'load') await showLoadDialog(ctx);
            else if(action === 'settings') await showSettingsDialog(ctx);
            else if(action === 'exit') showToast('Exit is handled by the launcher.');
        } catch(err) {
            console.error('Main menu action failed:', err);

            showToast(err?.message || 'Unexpected error');
        }
    });
}

async function showNewDialog(ctx) {
    const packs = await fetchAppPacks(ctx);
    if(!packs.length) {
        showToast('No app packs found.');
        return;
    }

    let selected = null;
    const list = buildList(packs, (pack) => `
        <strong>${pack.name}</strong><br />
        <small>${pack.id} · ${pack.version} · ${pack.rootKind ?? 'unknown'}</small>
    `);

    list.addEventListener('click', (ev) => {
        const row = ev.target.closest('.turnix-mm-dialog__item');
        if(!row) return;
        [...list.children].forEach((child) => child.dataset.selected = 'false');
        row.dataset.selected = 'true';
        selected = packs.find((pack) => pack.id === row.dataset.value) || null;
        if(selected && !runtimeInstanceIdInput.value) runtimeInstanceIdInput.value = randomRuntimeInstanceId();
    });

    const form = document.createElement('div');
    form.className = 'turnix-mm-form';
    const runtimeLabel = document.createElement('label');
    runtimeLabel.innerHTML = 'Save Name<input type="text" placeholder="auto" />';
    const runtimeInstanceIdInput = runtimeLabel.querySelector('input');
    const labelField = document.createElement('label');
    labelField.innerHTML = 'Label<input type="text" placeholder="My Adventure"> />';
    form.append(list, runtimeLabel, labelField);

    createDialog({
        title: 'Create new runtime',
        body: form,
        actions: [
            {label: 'Cancel', variant: 'secondary', onClick: (overlay) => overlay.remove()},
            {label: 'Create', onClick: async (overlay) => {
                if(!selected) {
                    showToast('Select an app pack first.');
                    return;
                }
                const payload = {
                    appPackId: selected.id,
                    runtimeInstanceId: runtimeInstanceIdInput.value?.trim() || undefined,
                    label: labelField.querySelector('input').value?.trim() || undefined,
                };
                await generateRuntime(ctx, payload);
                overlay.remove();
            }},
        ],
    });
}

async function showLoadDialog(ctx) {
    const saves = await fetchSaves(ctx);
    if(!saves.length) {
        showToast('No saves found.');
        return;
    }
    let selected = null;
    const list = buildList(saves, (save) => {
        const label = save.label ? ` · ${save.label}` : '';
        const ts = save.savedTs ? new Date(save.savedTs * 1000).toLocaleString() : 'unknown';
        return `<strong>${save.appPackId}</strong> / ${save.runtimeInstanceId}${label}<br /><small>${ts}</small>`;
    });
    list.addEventListener('click', (ev) => {
        const row = ev.target.closest('.turnix-mm-dialog__item');
        if(!row) return;
        [...list.children].forEach((child) => child.dataset.selected = 'false');
        row.dataset.selected = 'true';
        selected = saves.find((save) => `${save.appPackId}/${save.runtimeInstanceId}` === row.dataset.value) || null;
    });
    [...list.children].forEach((row, idx) => {
        const save = saves[idx];
        row.dataset.value = `${save.appPackId}/${save.runtimeInstanceId}`;
    });

    createDialog({
        title: 'Load save',
        body: list,
        actions: [
            {label: 'Cancel', variant: 'secondary', onClick: (overlay) => overlay.remove()},
            {label: 'Load', onClick: async (overlay) => {
                if(!selected) {
                    showToast('Select a save first.');
                    return;
                }
                await loadSave(ctx, {
                    appPackId: selected.appPackId,
                    runtimeInstanceId: selected.runtimeInstanceId,
                });
                showToast(`Loaded ${selected.appPackId}`);

                overlay.remove();
            }},
        ],
    });
}

async function showSettingsDialog() {
    const body = document.createElement('div');
    body.innerHTML = '<p>Settings will be here soon.</p>';
    createDialog({
        title: 'Settings',
        body,
        actions: [{label: 'Close', onClick: (overlay) => overlay.remove()}]
    });
}
