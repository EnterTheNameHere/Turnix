let socket = null;
let requestIdCounter = 1;
const pendingRequests = new Map();
const handlers = new Map();
let connected = false;

function connect(url = "ws://localhost:8000/ws") {
    socket = new WebSocket(url);

    socket.addEventListener("open", () => {
        connected = true;
        console.info("[RPC] Connected to backend.");
    });

    socket.addEventListener("message", async (event) => {
        try {
            const msg = JSON.parse(event.data);
            if(msg.type === "response" && pendingRequests.has(msg.requestId)) {
                const { resolve, reject } = pendingRequests.get(msg.requestId);
                pendingRequests.delete(msg.requestId);
                msg.success ? resolve(msg.data) : reject(msg.error);
            } else if(msg.type === "event") {
                const handler = handlers.get(msg.name);
                if(handler) await handler(msg.data);
            }
        } catch (err) {
            console.error("[RPC] Error handling message.", err);
        }
    });

    socket.addEventListener("close", () => {
        connected = false;
        console.warn("[RPC] Disconnected from backend. Reconnecting...");
        setTimeout(() => connect(url), 2000); // TODO: Do someting like asking user what to do...
    });

    socket.addEventListener("error", (err) => {
        console.error("[RPC] WebSocket error.", err);
        connected = false;
        socket.close();
    });
}

function send(name, data = {}, expectResponse = true) {
    if(!connect || socket.readyState !== WebSocket.OPEN) {
        console.warn(`[RPC] Not connected to backend. Dropping requrest '${name}'`);
        return expectResponse ? Promise.reject("Not connected") : null;
    }

    const id = requestIdCounter++;
    const message = {
        type: "request",
        id,
        name,
        data,
    };

    socket.send(JSON.stringify(message));

    if(expectResponse) {
        return new Promise((resolve, reject) => {
            pendingRequests.set(id, { resolve, reject });
            // Optional timeout for the response.
            setTimeout(() => {
                if(pendingRequests.has(id)) {
                    pendingRequests.delete(id);
                    reject("RPC timeout");
                }
            }, 5000); // TODO: make this configurable, repeatable, extendable timeout
        });
    } else {
        return null;
    }
}

function fire(name, data = {}) {
    send(name, data, false);
}

function on(name, handler) {
    handlers.set(name, handler);
}

function off(name, handler) {
    if(handler) {
        handlers.delete(name);
    }
}

export const rpc = {
    connect,
    send,
    fire,
    on,
    off,
}

function registerInternalRpcHandlers() {
    rpc.on("frontendHook", async (msg) => {
        const { name, stage, data, requestId } = msg;
        try {
            const result = await frontendBus.execute(stage, name, data);
            send("frontendHookResponse", {
                requestId,
                result,
                name,
            }, false);
        } catch(err) {
            console.error("[RPC] Frontend hook error:", err);
            send("frontendHookResponse", {
                requestId,
                error: err.toString(),
                name,
            }, false);
        }
    });
}

registerInternalRpcHandlers();
