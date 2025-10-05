let activeChatSub = null;

const Toast = {
    info:  ({text, ttlMs}) => showToast(text, ttlMs, 'info'),
    warn:  ({text, ttlMs}) => showToast(text, ttlMs, 'warn'),
    error: ({text, ttlMs}) => showToast(text, ttlMs, 'error'),
};

// ---------- Toast helper ----------
function showToast(text, ms = 2000, level='info') {
    // Reuse if possible
    let toasts = document.querySelector('.toasts');
    if(!toasts) {
        toasts = document.createElement('div');
        toasts.className = 'toasts';
        document.body.appendChild(toasts);
    }

    const toast = document.createElement('div');
    toast.className = `toast ${level}`;
    toast.textContent = String(text);
    toasts.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => toast.classList.add("show"));

    const hide = () => {
        toast.classList.remove("show");
        toast.addEventListener(
            'transitionend',
            () => {
                toast.remove();
                if(toasts.childElementCount === 0) toasts.remove();
            },
            { once: true }
        );
    };

    // Auto hide and click to dismiss
    const timer = setTimeout(hide, ms);
    toast.addEventListener('click', () => {
        clearTimeout(timer);
        hide();
    });
}

function appendMessage(role, text) {
    const el = document.createElement('div');
    el.className = `msg ${role}`;
    el.textContent = text;
    const chatEl = document.getElementById("chat");
    chatEl.appendChild(el);
    chatEl.scrollTop = chatEl.scrollHeight;
    return el;
}

export async function onLoad(ctx) {
    const appEl = document.getElementById('app');

    // Chat area
    const chatEl = document.createElement("div");
    chatEl.id = "chat";
    chatEl.className = "chat";

    // Composer
    const composerEl = document.createElement('div');
    composerEl.className = "composer";

    const textareaEl = document.createElement('textarea');
    textareaEl.id = "input";
    textareaEl.placeholder = 'Type a message...';

    const btnSend = document.createElement('button');
    btnSend.textContent = 'Send';

    const startStream = async (text) => {
        // Cancel any previous stream
        if(activeChatSub) {
            try { ctx.rpc.unsubscribe(activeChatSub.id); } catch {}
            activeChatSub = null;
        }

        // Render assistant bubble that we'll chunk-update
        const assistantEl = appendMessage("assistant", "");

        let buffer = "";

        btnSend.disabled = true;
        try {
            const sub = await ctx.rpc.subscribe(
                {capability: "chat@1"},
                "stream", // path; not used
                "none", // op; not used
                {role: "user", text: text} // opts → becomes payload on subscribe
            );
            activeChatSub = sub;

            sub.on("update", (payload) => {
                // Payload: {delta?: string, done?: boolean, text?: string}
                if(!payload) return;
                if(typeof payload.delta === "string") {
                    buffer += payload.delta;
                    assistantEl.textContent = buffer;
                    chatEl.scrollTop = chatEl.scrollHeight;
                }
                if(typeof payload.done === "boolean" && payload.done) {
                    // Prefer autorative full text
                    if(typeof payload.text === "string") {
                        buffer = payload.text;
                        assistantEl.textContent = buffer;
                        chatEl.scrollTop = chatEl.scrollHeight;
                    }
                    // Cleanup
                    try { ctx.rpc.unsubscribe(sub.id); } catch {}
                    if(activeChatSub && activeChatSub.id === sub.id) activeChatSub = null;
                    btnSend.disabled = false;
                }
            });
            sub.on("error", (payload) => {
                assistantEl.textContent = "Error: " + payload?.message ?? "unexpected issue occured";
                Toast.error({text: "Chat failed: " + payload?.message ?? "unknown reason", ttlMs: 3000});
                btnSend.disabled = false;
                // TODO: subscription might be still fine, or error is fatal - think of how to report it...
            });
        } catch(err) {
            assistantEl.textContent = "Error: " + (err?.message || err);
            Toast.error({text: "Chat failed: " + (err?.message || err), ttlMs: 3000});
            btnSend.disabled = false;
            // TODO: subscription might be still fine, or error is fatal - think of how to report it...
        }
    }

    const send = async () => {
        const hasText = typeof textareaEl.value === "string" && textareaEl.value.trim().length !== 0;
        const text = hasText ? textareaEl.value : "Hi! I'm UI, user didn't provide any message to send you... Introduce yourself as game master, ready to play a game with user. End it with a joke! Thank you, UI ends.";
        
        // Render user bubble immediately
        appendMessage("user", text);
        textareaEl.value = "";
        textareaEl.focus();

        await startStream(text);
    };

    btnSend.onclick = send;

    // Enter = send; Shift+Enter = newline
    textareaEl.addEventListener("keydown", (ev) => {
        if(ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            btnSend.click();
        }
    });

    // Show a toast in the browser when backend asks
    const disposeToast = await ctx.rpc.expose("ui.toast@1", {
        call: async(_path, args) => {
            console.log('ui.toast@1 - call is called!');
            const [text = "Hello from backend!", ms = 1500, level = "info"] = args || [];
            showToast(text, ms, level);
            return {ok: true};
        }
    });

    // Subscribe to get latest time!
    ctx.rpc.expose("time.service@1", {
        subscribe: async(_path, _opts, ctx2) => {
            // Push time until cancelled
            let timer = setInterval(() => ctx2.push({ now: Date.now() }), 2500);
            ctx2.signal.addEventListener("abort", () => clearInterval(timer));
            return {initial: { now: Date.now() }, onCancel: () => clearInterval(timer)}
        }
    });

    composerEl.append(textareaEl, btnSend);
    appEl.append(chatEl, composerEl);
}
