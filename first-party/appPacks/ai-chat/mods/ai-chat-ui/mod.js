// first-party/appPacks/ai-chat/mods/ai-chat-ui/mod.js

const layout = await turnixImport('/core/layout.js');

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
    toast.addEventListener('click', () => { clearTimeout(timer); hide(); });
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

    const btnStop = document.createElement('button');
    btnStop.textContent = 'Stop';
    btnStop.disabled = true;

    let lastRunId = null;
    let stageSub = null;
    let currentAssistantEl = null;
    let currentUserTempEl = null;
    let lastUserEl = null;
    let lastUserText = '';

    // Keep scroll pinned to bottom only if user is already near bottom
    const isAtBottom = () => {
        const slack = 48; // Tolerance before auto-stick in px.
        return chatEl.scrollTop + chatEl.clientHeight >= chatEl.scrollHeight - slack;
    };

    const autoscroll = () => { chatEl.scrollTop = chatEl.scrollHeight; };

    // Send: starts pipeline via llm.pipeline@1/run
    const send = async () => {
        let runStarted = false;
        const hasText = typeof textareaEl.value === 'string' && textareaEl.value.trim().length !== 0;
        const text = hasText ? textareaEl.value : 'Hi! I\'m UI, user didn\'t provide any message to send you... Introduce yourself as game master, ready to play a game with user. End it with a joke! Thank you, UI ends.';
        
        // Optimistic bubble
        const tempEl = document.createElement('div');
        tempEl.className = 'msg user';
        tempEl.dataset.temp = 'true';
        tempEl.textContent = text;
        chatEl.appendChild(tempEl);
        autoscroll();
        currentUserTempEl = tempEl;
        lastUserEl = tempEl;
        // Only keep real user messages for retry.
        lastUserText = hasText ? text : '';

        textareaEl.value = '';
        textareaEl.focus();

        btnSend.disabled = true;
        try {
            const reply = await ctx.rpc.request(
                {
                    route: {capability: 'llm.pipeline@1'},
                    path: 'run',
                    op: 'call',
                    args: [{ threadId: 'default', userText: text, options: {} }],
                },
                {},
                { class: 'request.medium' },
            );
            const runId = reply?.runId ?? null;
            if(runId) {
                runStarted = true;
                lastRunId = runId;
                // Keep the optimistic user bubble (make it permanent)
                if(currentUserTempEl) {
                    delete currentUserTempEl.dataset.temp;
                    currentUserTempEl = null;
                }

                btnStop.disabled = false; // Streaming → allow cancel
                
                currentAssistantEl = document.createElement('div');
                currentAssistantEl.className = 'msg assistant';
                currentAssistantEl.dataset.oid = `assistant:${runId}`;
                currentAssistantEl.dataset.status = 'streaming';
                currentAssistantEl.textContent = '';
                chatEl.appendChild(currentAssistantEl);
                if(isAtBottom()) autoscroll();

                if(stageSub) {
                    try {
                        stageSub.close();
                    } catch {
                        /* Ignored */
                    }
                    stageSub = null;
                }
                stageSub = await ctx.rpc.subscribe(
                    {capability: 'llm.pipeline@1'},
                    'stage',
                    'none',
                    {runId, stage: 'ParseStreamedResponse'},
                );
                stageSub.on('update', (msg) => {
                    console.debug('[ExampleMod] stage update:', msg);
                    if(!msg || !currentAssistantEl) return;

                    if(msg.kind === 'chunk') {
                        const delta = typeof msg.deltaText === 'string' ? msg.deltaText : '';
                        if(delta) {
                            currentAssistantEl.textContent = (currentAssistantEl.textContent || '') + delta;
                            if(isAtBottom()) autoscroll();
                        }
                        if(msg.fields?.status) currentAssistantEl.dataset.status = msg.fields.status;
                        return;
                    }
                    
                    if(msg.kind === 'done' || msg.kind === 'error') {
                        const isError = msg.kind === 'error';
                        const hasText = !!(currentAssistantEl?.textContent
                                        && currentAssistantEl.textContent.trim().length);
                        
                        if(msg.fields?.status) currentAssistantEl.dataset.status = msg.fields.status;
                        currentAssistantEl.dataset.status =
                            currentAssistantEl.dataset.status || (isError ? 'error' : 'final');
                        
                        btnStop.disabled = true;
                        lastRunId = null;
                        
                        try { stageSub?.close?.(); } catch {/* Ignore */}
                        stageSub = null;

                        if(isError && !hasText) {
                            // Hard failure before any visible output → restore input
                            if(currentAssistantEl && currentAssistantEl.parentNode === chatEl) {
                                chatEl.removeChild(currentAssistantEl);
                            }
                            currentAssistantEl = null;

                            if(lastUserEl && lastUserEl.parentNode === chatEl) {
                                chatEl.removeChild(lastUserEl);
                            }
                            lastUserEl = null;

                            if(lastUserText) {
                                textareaEl.value = lastUserText;
                                textareaEl.focus();
                            }
                        } else {
                            // Either success, or error after some output → keep bubbles
                            lastUserText = '';
                            lastUserEl = null;
                            currentAssistantEl.dataset.status =
                                currentAssistantEl.dataset.status || (isError ? 'error' : 'final');
                        }

                        btnSend.disabled = false;
                    }
                });

                stageSub.on?.('error', (err) => {
                    console.error('[ExampleMod] stage subscribe error', err);
                    Toast.error({text: `Stream error: ${err?.message || err}`, ttlMs: 3000});
                    
                    btnStop.disabled = true;
                    lastRunId = null;
                    
                    const hasText = !!(currentAssistantEl?.textContent
                                    && currentAssistantEl.textContent.trim().length);
                    
                    if(!hasText) {
                        // Only nuke bubbles if nothing visible was produced
                        if(currentAssistantEl && currentAssistantEl.parentNode === chatEl) {
                            chatEl.removeChild(currentAssistantEl);
                        }
                        currentAssistantEl = null;

                        if(lastUserEl && lastUserEl.parentNode === chatEl) {
                            chatEl.removeChild(lastUserEl);
                        }
                        lastUserEl = null;

                        if(lastUserText) {
                            textareaEl.value = lastUserText;
                            textareaEl.focus();
                        }
                    } else {
                        // Keep partial answer. Just mark assistant as errored
                        if(currentAssistantEl) {
                            currentAssistantEl.dataset.status =
                                currentAssistantEl.dataset.status || 'error';
                        }
                        lastUserText = '';
                        lastUserEl = null;
                    }

                    btnSend.disabled = false;
                    try { stageSub?.close?.(); } catch {/* Ignore */}
                    stageSub = null;
                });
            } else {
                // No run started → allow re-send
                btnSend.disabled = false;
                // Remove optimistic bubble as nothing actually started
                chatEl.querySelectorAll('.msg.user[data-temp="true"]').forEach((item) => item.remove());
            }
            console.debug('[ExampleMod] start runId', runId);
        } catch(err) {
            console.error('[ExampleMod] startQuery error', err);
            Toast.error({text: `Start failed: ${err?.message || err}`, ttlMs: 3000});
        } finally {
            // This is the "run failed to start at all" path.
            if(!runStarted) {
                btnSend.disabled = false;
                chatEl.querySelectorAll('.msg.user[data-temp="true"]').forEach((item) => item.remove());
                currentUserTempEl = null;
                lastUserEl = null;
                if(lastUserText) {
                    textareaEl.value = lastUserText;
                    textareaEl.focus();
                }
            }
        }
    };

    btnSend.onclick = send;

    btnStop.onclick = async () => {
        if(!lastRunId) return;
        try {
            btnStop.disabled = true; // Prevent double-click spam. Button resets on runCompleted
            await ctx.rpc.request(
                {
                    route: {capability: 'llm.pipeline@1'},
                    path: 'cancel',
                    op: 'call',
                    args: [{runId: lastRunId}]},
                {},
                { class: 'request.low' }
            );
        } catch(err) {
            console.error('[ExampleMod] cancel error', err);
            Toast.error({text: `Cancel failed: ${err?.message || err}`, ttlMs: 3000});
            // If cancel failed, re-enable Stop so user can try again
            btnStop.disabled = false;
        }
    };

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

    // reset.layout@1 - Reset panel sizes to stylesheet defaults
    await ctx.rpc.expose('reset.layout@1', {
        call: async(_path, _args) => {
            const sizes = layout.resetLayoutSizes();
            Toast.info({text: 'Layout reset to defaults', ttlMs: 1500});
            return {ok: true, sizes};
        }
    });

    const resetChatLog = (node) => {
        while(node.firstChild) node.removeChild(node.firstChild);
        return {cleared: true};
    };
    
    // reset.chat@1 - Clear current chat log
    await ctx.rpc.expose('reset.chat@1', {
        call: async(_path, _args) => {
            const res = resetChatLog(chatEl);
            Toast.info({text: 'Chat cleared', ttlMs: 1200 });
            return {ok: true, ...res};
        }
    });

    composerEl.append(textareaEl, btnSend, btnStop);
    appEl.append(chatEl, composerEl);

    addEventListener('beforeunload', () => {
        try { if(stageSub) ctx.rpc.unsubscribe(stageSub.id); } catch {/* Ignored */}
    });


    // ---------- Streaming-only UI (no thread subscription) ----------
    // History is owned by backend chat-history. This mod shows only the current turn stream.
    // Remove thread subscription & delta logic.

    // Enable draggable panel resizers
    layout.initResizers();
    // Auto-collapse empty panels
    layout.initAutoCollapsePanels();
}
