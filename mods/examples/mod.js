let counter = 0;

// ---------- Toast helper ----------
function showToast(message, ms = 2000) {
    // Reuse if possible
    let wrapper = document.getElementById('toast-wrapper');
    if(!wrapper) {
        wrapper = document.createElement('div');
        wrapper.id = 'toast-wrapper';
        Object.assign(wrapper.style, {
            position: 'fixed',
            bottom: '16px',
            left: '50%',
            transform: 'translate(-50%)',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            zIndex: '9999',
            pointerEvents: 'none', // click pass through unless on a toast itself
        });
        document.body.appendChild(wrapper);
    }

    const toast = document.createElement('div');
    Object.assign(toast.style, {
        maxWidth: 'min(90vw, 520px)',
        padding: '10px 14px',
        background: 'rgba(32,32,32,0.95)',
        color: '#fff',
        borderRadius: '10px',
        boxShadow: '0 6px 18px rgba(0,0,0,0.25)',
        font: '14px/1.35',
        opacity: '0',
        transform: 'translateY(8px)',
        transition: 'opacity 180ms ease, trasform 180ms ease',
        pointerEvents: 'auto', // clickable to dismiss
        userSelect: 'none',
        whiteSpace: 'pre-wrap',
    });
    toast.textContent = String(message);
    wrapper.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
    });

    const hide = () => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(8px)';
        toast.addEventListener(
            'transitionend',
            () => {
                toast.remove();
                if(wrapper.childElementCount === 0) wrapper.remove();
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


export async function onLoad(ctx) {
    const app = document.getElementById('app');

    const wrapper = document.createElement('div');
    wrapper.style.marginTop = '1rem';

    const input = document.createElement('input');
    input.placeholder = 'attack / defend / sneak';
    input.style.marginRight = '.5rem';

    const btn1 = document.createElement('button');
    btn1.textContent = 'Send to GM';
    btn1.style.marginTop = '1rem';

    const btn2 = document.createElement('button');
    btn2.textContent = 'Ping HTTP (httpbin.org/get)';
    btn2.style.marginTop = '1rem';

    const btn3 = document.createElement('button');
    btn3.textContent = 'Make backend call us';
    btn3.style.marginTop = '1rem';

    const btn4 = document.createElement('button');
    btn4.textContent = 'Backend subscribes to us';
    btn4.style.marginTop = '1rem';

    const btn5 = document.createElement('button');
    btn5.textContent = 'Backend unsubscribes';
    btn5.style.marginTop = '1rem';

    const log = document.getElementById('log');

    function logLine(str) { log.textContent += str + '\n'; }

    btn1.onclick = async () => {
        const action = input.value || 'wait';
        try {
            const res = await ctx.rpc.request(
                { capability: 'gm.narration@1' },
                'turn',
                [action],
                { class: 'request.medium' }
            );
            logLine('GM: ' + res.text);
        } catch(ex){
            logLine('Error: ' + ex.message);
        }
    };

    btn2.onclick = async () => {
        try {
            const res = await ctx.rpc.request(
                { capability: 'http.client@1' },
                'request',
                ['GET', 'https://httpbin.org/get', {}],
                { class: 'request.medium' }
            );
            logLine(`HTTP status: ${res.status}`);
        } catch(ex) {
            logLine(`HTTP error: ${ex.message}`)
        }
    };

    btn3.onclick = async () => {
        await ctx.rpc.emit(
            { capability: 'test.sendText@1' }
        );
    };

    btn4.onclick = async () => {
        await ctx.rpc.emit(
            { capability: 'test.subscribe@1' }
        );
    }

    btn5.onclick = async () => {
        await ctx.rpc.emit(
            { capability: 'test.unsubscribe@1' }
        );
    }

    // Show a toast in the browser when backend asks
    const disposeToast = await ctx.rpc.expose("ui.toast@1", {
        call: async(_path, args) => {
            console.log('ui.toast@1 - call is called!');
            const [text = "Hello from backend!", ms = 1500] = args || [];
            showToast(text, ms);
            return {ok: true};
        }
    });

    // time.service@1 handler
    const handler = (payload) => {

    }

    // Subscribe to get latest time!
    ctx.rpc.expose("time.service@1", {
        subscribe: async(_path, _opts, ctx2) => {
            // Push time until cancelled
            let timer = setInterval(async () => await ctx2.push({ now: Date.now() }), 2500);
            ctx2.signal.addEventListener("abort", () => clearInterval(timer));
            return { initial: { now: Date.now() }, onCancel: () => clearInterval(timer) }
        }
    });

    // Subscribe to world state demo
    const sub = await ctx.rpc.subscribe({ capability: 'gm.world@1' }, 'stateStream', { class: 'stream.default' });
    sub.on('update', delta => {
        logLine('[world] ' + JSON.stringify(delta));
        counter++;
        if(counter > 5) {
            // And unsubscribe
            ctx.rpc.unsubscribe(sub.id);
        };
    });

    wrapper.append(input, btn1, btn2, btn3, btn4, btn5);
    app.appendChild(wrapper);
}
