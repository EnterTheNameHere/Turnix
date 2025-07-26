let socket = null;
let requestIdCounter = 1;
const pendingRequests = new Map();
const handlers = new Map();
let connected = false;
let reconnectionCounter = 0;

export function getSocket() {
    return socket;
}

function makeRequestId() {
    return `${window.__viewId}@${window.__clientId}#js:${requestIdCounter++}`;
}

function isConnected() {
    return connected && socket?.readyState === WebSocket.OPEN;
}

function shutdown() {
    if(socket && socket.readyState === WebSocket.OPEN) {
        socket.close();
    }
    connected = false;
    handlers.clear();
    pendingRequests.clear();
}

function connect(url = "ws://localhost:8000/viewws") {
    socket = new WebSocket(url);

    socket.addEventListener("open", (event) => {
        connected = true;
        reconnectionCounter = 0;
        //console.debug("[RPC] Connected to backend.", event);

        fire("identifyView")
    });

    // Main WebSocket message handler for View
    socket.addEventListener("message", async (event) => {
        //console.debug("[RPC] Received message.", event)

        let message = null;
        try {
            message = JSON.parse(event.data);
        } catch(err) {
            console.error("[RPC] Failed to parse incoming message:", event.data);
            error({
                code: "UNABLE_TO_PARSE",
                errorMessage: "Frontend cannot parse incoming message",
                action: "[unknown]",
                requestId: "[unknown]",
                viewId: "[unknown]",
                clientId: "[unknown]",
                securityToken: "[unknown]",
            });
            return;
        }

        switch(message.type) {
            case "frontendReply":
            case "frontendRequest":
            case "frontendEmit":
            case "frontendError": {
                console.error(`[RPC] Wrong type "${message.type}" of incoming message.`)
                break;
            }

            case "backendRequest": {
                // Backend sends request and expects a reply
                if(!message.action) {
                    console.error("[RPC] Incoming message in backend request is missing action field.");
                    error({
                        ...message,
                        code: "MISSING_FIELD",
                        errorMessage: "Incoming message in backend request is missing action field.",
                    });
                    return;
                } else if(!handlers.has(message.action)) {
                    console.error(`[RPC] Handler for action "${message.action}" not found.`);
                    error({
                        ...message,
                        code: "HANDLER_NOT_FOUND",
                        errorMessage: `Handler for action "${message.action}" not found.`,
                    });
                    return;
                } else {
                    try {
                        const handler = handlers.get(message.action);
                        //console.debug(`[RPC] Executing handler for action "${message.action}"`);
                        const result = await handler(message.data, message);
                        //console.debug(`[RPC] Handler for action "${message.action}" returned`, result);

                        send({
                            ...message,
                            type: "frontendReply",
                            data: result,
                        });
                    } catch(err) {
                        const { ModWrappedError } = await turnixImport("./modloader.js");
                        let modId = null;
                        if(err instanceof ModWrappedError) {
                            modId = err.modId;
                        }
                        console.error(`[RPC] Handler for action "${message.action}" thrown error:\n`, err);
                        error({
                            ...message,
                            ...(message?.origin?.modId && {origin: { modId: message.origin.modId }}),
                            code: "HANDLER_ERROR",
                            errorMessage: err?.toString?.() || "Unknown handler error.",
                            modId: modId,
                        })
                        return;
                    }
                }
                break;
            }

            case "backendEmit": {
                if(!message.action) {
                    console.warn("[RPC] Backend emit is missing action field.");
                    return;
                } else if(!handlers.has(message.action)) {
                    console.warn(`[RPC] Handler for action "${message.action}" not found.`)
                    return
                } else {
                    try {
                        const handler = handlers.get(message.action);
                        await handler(message.data, message);
                    } catch(err) {
                        console.warn(`[RPC] Error in emit handler for action "${message.action}".`, err);
                    }
                }

                break;
            }

            case "backendReply": {
                const pending = pendingRequests.get(message.requestId);
                if(!pending) {
                    console.warn(`[RPC] No pending request found for reply id "${message.requestId}".`)
                    return;
                } else {
                    pendingRequests.delete(message.requestId);
                    pending.resolve(message.data, message);
                }
                
                break;
            }

            case "backendError": {
                const pending = pendingRequests.get(message.requestId);
                if(!pending) {
                    console.warn(`[RPC] No pending request found for reported error "${message.requestId}".`)
                    return;
                } else {
                    pendingRequests.delete(message.requestId);
                    const err = new Error(message?.error?.message || "Unknown RPC error.");
                    err.code = message?.error?.code;
                    err.details = message?.error?.details;
                    pending.reject(err, message);
                }

                break;
            }

            default: {
                console.warn(`[RPC] Unknown message type "${message.type}".`)
            }
        }
    });

    socket.addEventListener("close", () => {
        connected = false;
        if(reconnectionCounter++ < 4) {
            console.warn("[RPC] Disconnected from backend. Reconnecting...");
            setTimeout(() => connect(url), 2000); // TODO: Do someting like asking user what to do...
        } else {
            console.error("[RPC] Disconnected from backend. Giving up.");
            socket.close();
        }
    });

    socket.addEventListener("error", (err) => {
        console.error("[RPC] WebSocket error.", err);
        connected = false;
        socket.close();
    });
}

function send({
    type,
    action,
    requestId = null,
    viewId = window.__viewId,
    clientId = window.__clientId,
    securityToken = window.securityToken,
    data,
    modId = null})
{
    if(!isConnected()) {
        console.warn(`[RPC] Not connected to backend. Dropping requrest '${action}' with id '${requestId}'`);
        if(type === "frontendRequest") {
            const err = new Error("Not connected");
            err.code = "NOT_CONNECTED";
            return Promise.reject(err);
        } else {
            return Promise.resolve(null);
        }
    }

    let _requestId = requestId;
    if(type === "frontendRequest") {
        _requestId = makeRequestId();
    }
    const message = {
        type,
        action,
        viewId,
        clientId,
        securityToken,
        timestamp: Date.now(),
        requestId: _requestId,
        data,
    };
    if(modId) {
        message.origin = { modId: modId };
    }

    //console.trace("[RPC] Sending message", message);

    socket.send(JSON.stringify(message));

    if(type === "frontendRequest") {
        return new Promise((resolve, reject) => {
            pendingRequests.set(_requestId, { resolve, reject });
            // Optional timeout for the response.
            setTimeout(() => {
                if(pendingRequests.has(_requestId)) {
                    pendingRequests.delete(_requestId);
                    const err = new Error("RPC timeout");
                    err.code = "TIMEOUT";
                    reject(err);
                }
            }, 5000); // TODO: make this configurable, repeatable, extendable timeout
        });
    } else {
        return null;
    }
}

function error({
    code,
    errorMessage,
    action,
    requestId,
    viewId = window.__viewId,
    clientId = window.__clientId,
    securityToken = window.securityToken,
    modId = null,
}) {
    let message = {
        type: "frontendError",
        action: action,
        viewId: viewId,
        clientId: clientId,
        securityToken: securityToken,
        requestId: requestId,
        timestamp: Date.now(),
        error: {
            code: code,
            message: errorMessage,
        },
    };

    if(modId) {
        message.error.origin = { modId: modId };
    }

    //console.debug("[RPC] Sending error message:", message);

    socket.send(JSON.stringify(message));
}

function fire(action, data = {}) {
    return send({ type: "frontendEmit", action, data });
}

function on(action, handler) {
    //console.debug(`[RPC] Registering handler for action ${action}`)
    if(handlers.has(action)) {
        console.warn(`[RPC] Handler for action ${action} already registered`);
    }
    handlers.set(action, handler);
}

function off(action) {
    handlers.delete(action);
}

export const rpc = {
    connect, send, fire, on, off, isConnected, shutdown
};

function registerInternalRpcHandlers() {
    //rpc.on("loadJSMod", async (manifest) => {
    //    console.log("loadJSMod", manifest);
    //});
}

registerInternalRpcHandlers();
