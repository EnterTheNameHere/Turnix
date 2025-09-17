import { uuidv7 } from "uuidv7";

export class PerLaneSerializer {
    constructor() {
        this.tails = new Map();
    }

    enqueue(lane, task) {
        const tail = this.tails.get(lane) || Promise.resolve();
        const next = tail.then(() => task()).catch(err => {
            console.error("[lane]", lane, err);
        });
        this.tails.set(lane, next.finally(() => {
            if(this.tails.get(lane) === next) {
                this.tails.delete(lane);
            }
        }));
    }
}

export class RpcClient {
    static async connect(url, {token, settings} = {}) {
        const client = new RpcClient(url, settings ?? defaultSettings(), token);
        client.token = token;
        await client.#open();
        return client;
    }

    #generation = 0; // Connection generation epoch

    // TODO: Send settings if updated to backend, and receive settings from backend
    constructor(url, settings, token) {
        this.url = url;
        this.token = token;
        this.settings = settings || defaultSettings();

        this.webSocket = null;
        this.connected = false;
        this.reconnectAttempts = 0;
        this.#generation = 0;
        
        this.offlineQueue = [];          // Used to collect messages when connection is not OPEN; sent once connection is OPEN
        this.laneStates = new Map();     // lane -> { nextSeq, inFlight }
        this.pending = new Map();        // id -> { resolve, reject, timer, opts }
        /** @type {Map<uuidv7, import("./types").Subscription} */
        this.subscriptions = new Map();  // subId -> subscription object
        this.localCaps = new Map();      // capability -> { call?, emit?, subscribe? }

        this.heartbeatTimer = null;
        this.awolTimer = null;

        this.heartbeat = { timer: null, intervalMs: this.settings.protocol.heartbeatMs, lastSeen: Date.now() };
    }

    /**
     * @typedef {object} CapabilityHandlers
     * @property {(path: string, args, ctx) => Promise<any>} [call]
     * @property {(path: string, payload, ctx) => void} [emit]
     * @property {(path: string, opts = {}, ctx) => { "onCancel"?: () => void, push: (payload) => void }} [subscribe]
     */

    #now() { return Math.floor(performance.now()); }

    async #connectOnce() {
        this.webSocket = new WebSocket(this.url);
        const ws = this.webSocket;

        await new Promise((resolve, reject) => {
            ws.onopen = () => resolve();
            ws.onerror = (ex) => reject(ex);
        });

        // Keep handlers AFTER open so we don't miss events
        ws.onmessage = (ev) => this.#onMessage(ev);
        ws.onclose = () => this.#onClose();

        this.connected = true;
        this.reconnectAttempts = 0;

        // Send hello
        this.#sendRaw(this.#createHelloMessage());

        // Resume subscriptions (best effort)
        for(const [subId, subscription] of this.subscriptions.entries()) {
            // Create a new subscribe with a new id, but keep the local sub object
            const msg = this.#createSubscribeMessage({
                route: subscription.invocation.route,
                op: subscription.invocation.op ?? "none",
                path: subscription.invocation.path,
                payload: { ...subscription.opts },
            }, subscription.opts);

            // Move the sub to the new id
            this.subscriptions.delete(subId);
            this.subscriptions.set(msg.id, subscription);
            // Send and wait only for ACK so we don't stall whole connect
            this.#sendWithAck(msg).catch(() => {});
        }
    }

    #onClose() {
        this.connected = false;
        this.#stopHeartbeat();

        for(const [_id, pend] of this.pending.entries()) {
            clearTimeout(pend.timer);
            pend.reject(new Error("DISCONNECTED"));
            pend.onFinally?.();
        }
        this.pending.clear();

        // Subscriptions are kept for resuming later

        const base = 250, max = 3000;
        const attempt = ++this.reconnectAttempts;
        const delay = Math.min(max, base * Math.pow(2, attempt - 1)) + Math.floor(Math.random() * 100);
        setTimeout(() => {
            this.#connectOnce()
                .then(() => this.#flushQueue())
                .catch(() => this.#onClose()); // If we fail, try again
        }, delay);
    }

    request(route, path, args = [], opts = {}) {
        const msg = this.#createRequestMessage({ route, path, args }, opts);
        return this.#sendWithReply(msg, msg.lane, opts);
    }

    emit(route, path, payload = {}, opts = {}) {
        this.#enqueue(this.#createEmitMessage({ route, path, payload }, opts));
    }

    async subscribe(route, path, op, opts = {}) {
        console.group("Subscribe");
        console.log({route, path, op, opts});
        
        const msg = this.#createSubscribeMessage({ route, path, op }, opts);
        const subscription = this.#makeSub(msg.id, { route, path, op }, opts);
        console.log({subscription});
        this.subscriptions.set(msg.id, subscription);
        await this.#sendWithAck(msg);
        console.groupEnd()
        return subscription;
    }

    unsubscribe(subId, opts = {}) {
        console.group("Unsubscribe");
        console.log({subId, opts});
        const msg = this.#createUnsubscribeMessage({
            correlatesTo: subId,
        }, opts);
        this.#enqueue(msg);
        this.subscriptions.delete(subId);
        console.groupEnd();
    }

    cancel(correlatesTo, opts = {}) {
        const msg = this.#createCancelMessage({
            correlatesTo,
        }, opts);
        this.#enqueue(msg);
    }

    #asPromise(fn, ...args) {
        try {
            return Promise.resolve(fn(...args)); // Catches sync return or async promise
        } catch(err) {
            return Promise.reject(err);          // Catches sync throw
        }
    }

    #onMessage(ev) {
        this.#logIncomingStr(ev.data);
        /** @type {import("./types").RPCMessage} */
        const msg = JSON.parse(ev.data);

        // Frontend routing: backend is calling our exposed capability
        if(["request", "emit", "subscribe"].includes(msg.type) && msg.route?.capability) {
            const cap = this.localCaps.get(msg.route.capability);
            if(cap) {
                // Ack immediately
                this.#enqueue(this.#createACKMessage(msg));

                // For a request, we want to execute a call()
                if(msg.type === "request" && typeof cap.call === "function") {
                    Promise.resolve()
                        .then(() => cap.call(msg.path, msg.args || [], { origin: msg.origin, id: msg.id }))
                        .then((payload) => {
                            const message = this.#createReplyMessage(msg, {
                                payload: payload,
                            });
                            this.#enqueue(message);
                            return;
                        })
                        .catch((err) => {
                            const message = this.#createErrorMessage(msg, {
                                code: "FRONTEND_ERROR",
                                message: String(err),
                                err: err,
                                retryable: false,
                            });
                            this.#enqueue(message);
                            return;
                        });
                    return;
                }
                // For an emit, we want to execute an emit()
                else if(msg.type === "emit" && typeof cap.emit === "function") {
                    try {
                        cap.emit(msg.path, msg.payload, { origin: msg.origin, id: msg.id });
                    } catch(err) {
                        // Emit is non reporting, so just log it
                        console.warn(`Error caught when executing capability '${msg.path}' emit, error ${err.message}`, err, msg);
                    }
                    return;
                }
                // For a subscribe, we want to execute a subscribe()
                if(msg.type === "subscribe" && typeof cap.subscribe === "function") {
                    try {
                        console.log("Capability object:", cap);
                        if(!cap.subscribe) {
                            // TODO: Think about whether subscribe should be required, and how to report it - in frontend, in backend, in both?
                            this.#enqueue(this.#createErrorMessage(msg, {
                                code: "FRONTEND_SUBSCRIBE_ERROR",
                                message: "Capability does not have subscribe method",
                                err: err,
                                replyable: false,
                            }));
                        }

                        console.log("Subscriptions:", this.subscriptions);

                        const push = async (payload) => {
                            const message = this.#createStateUpdateMessage(msg, {payload});
                            this.#enqueue(message);
                        }
                        const abortController = new AbortController();
                        /** @type {import("./types").SubscribeCtx} */
                        const ctx = { id: msg.id, origin: msg.origin, signal: abortController.signal, push };

                        Promise.resolve()
                            .then(() => cap.subscribe(msg.path, msg.payload ?? {}, ctx))
                            .then((stream) => {
                                let subscription = this.subscriptions.get(msg.id);
                                if(!subscription) {
                                    subscription = this.#makeSub(msg.id, { route: msg.route, path: msg.path, op: msg.op }, {});
                                    this.subscriptions.set(msg.id, subscription);
                                }

                                console.log("Subscriptions:", this.subscriptions);

                                subscription._jsOnCancel = stream?.onCancel ?? null;

                                // If handler didn't provide its own push, use our
                                if(stream && typeof stream.push !== "function") {
                                    // Non-enumerable to prevent JSON serialization
                                    Object.defineProperty(stream, "push", { value: push, enumerable: false });
                                }

                                // Send initial data if provided
                                if(stream?.initial !== undefined) push(stream.initial);
                            })
                            .catch((err) => {
                                this.#enqueue(this.#createErrorMessage(msg, {
                                    code: "FRONTEND_SUBSCRIBE_ERROR",
                                    message: String(err),
                                    err: err,
                                    replyable: false,
                                }));
                            });
                    } catch(err) {
                        this.#enqueue(this.#createErrorMessage(msg, {
                            code: "FRONTEND_SUBSCRIBE_ERROR",
                            message: String(err),
                            err: err,
                            replyable: false,
                        }));
                    }
                    return;
                }
            }
        }

        // auto ack for non-control
        if(!["ack", "welcome", "hello", "heartbeat"].includes(msg.type)) {
            this.#enqueue(this.#createACKMessage(msg));
        }

        if(msg.type === "reply" || msg.type === "error") {
            const pending = this.pending.get(msg.correlatesTo);
            if(pending) {
                clearTimeout(pending.timer);
                this.pending.delete(msg.correlatesTo);
                try {
                    if(msg.type === "error") pending.reject(new Error(msg.payload?.message || "ERROR"));
                    else pending.resolve(msg.payload);
                } finally {
                    pending.onFinally?.();
                }
            }
            return;
        }

        if(msg.type === "stateUpdate") {
            const sub = this.subscriptions.get(msg.correlatesTo);
            sub?._emit("update", msg.payload);
            return;
        }

        if(msg.type === "ack") {
            const pending = this.pending.get(msg.correlatesTo);
            if(pending && pending.wantAck) {
                clearTimeout(pending.timer);
                pending.resolve(true);
                this.pending.delete(msg.correlatesTo);
            }
            return;
        }

        if(msg.type === "cancel" || msg.type === "unsubscribe") {
            console.log("Subscriptions:", this.subscriptions);
            console.log("Looking for subscription ", msg.correlatesTo);
            const subscription = this.subscriptions.get(msg.correlatesTo);
            console.log("Sub", subscription);

            if(subscription && typeof subscription._jsOnCancel === "function") {
                console.log(`Calling cancel on subscription ${msg.correlatesTo}`);
                try {
                    subscription._jsOnCancel(msg);
                } catch(err) {
                    console.warn(`Error caught during cancelling of subscription, error: ${err.message}`, err, msg);
                }
            }
            this.subscriptions.delete(subscription.id);
            return;
        }
    }

    #sendWithReply(msg, lane, opts) {
        const cap = this.settings.protocol.maxInFlightPerLane ?? 64;
        const laneState = this.laneStates.get(lane) || { nextSeq: 1, peerAck: 0, inFlight: 0 };
        if(laneState.inFlight >= cap) {
            return Promise.reject(new Error("LANE_BACKPRESSURE"));
        }
        laneState.inFlight++;
        this.laneStates.set(lane, laneState);

        return new Promise((resolve, reject) => {
            const classCfg = this.#resolveClassCfg(opts);
            const waitMs = Math.min(
                (msg.budgetMs ?? classCfg.serviceTtlMs) + (classCfg.clientPatienceExtraMs ?? 200),
                (this.settings.http?.timeoutCapMs ?? 30000)
            );
            const timer = setTimeout(() => {
                if(msg?.origin)
                    // Used only for tracing/logging, not for auth
                    this.cancel(msg.id, {origin: msg.origin});
                else
                    this.cancel(msg.id);
                reject(new Error("TIMEOUT"));
            }, waitMs);
            this.pending.set(msg.id, { resolve, reject, timer, laneKey: lane, onFinally: () => {
                const laneState = this.laneStates.get(lane) || { inFlight: 1 };
                laneState.inFlight = Math.max(0, (laneState.inFlight || 1) - 1);
                this.laneStates.set(lane, laneState);
            }});
            this.#enqueue(msg);
        });
    }

    #sendWithAck(msg) {
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error("NO_ACK")), this.settings.protocol.ackWaitMs);
            this.pending.set(msg.id, { resolve, reject, timer, laneKey: msg.lane, wantAck: true });
            this.#enqueue(msg);
        });
    }

    #logOutgoingStr(str) {
        if(!str) debugger;

        // If debugged
        if(this.settings.debug.frontend.rpc.outgoingMessages.log) {
            // and message is not of ignored type
            if(this.settings.debug.frontend.rpc.outgoingMessages.ignoreTypes.every(element => !str.includes(`"type":"${element}"`)))
                console.log(`[RPC] sending: ${str}`);
        }
    }

    #logIncomingStr(str) {
        // If debugged
        if(this.settings.debug.frontend.rpc.incomingMessages.log) {
            // and message is not of ignored type
            if(this.settings.debug.frontend.rpc.incomingMessages.ignoreTypes.every(element => !str.includes(`"type":"${element}"`)))
                console.log(`[RPC] incoming: ${str}`);
        }
    }

    #enqueue(obj) {
        if(this.webSocket && this.webSocket.readyState === WebSocket.OPEN) {
            this.#sendRaw(obj)
        } else {
            this.offlineQueue.push(obj);
        }
    }

    #flushQueue() {
        if(!this.webSocket || this.webSocket.readyState !== WebSocket.OPEN) return;
        for(const obj of this.offlineQueue.splice(0)) {
            this.#sendRaw(obj);
        }
    }

    /**
     * Automatically construct ACK message for given `toMsg`. `props` object
     * can be used to override the properties of final ACK message by custom values.
     * `opts` can be used to provide data for creator to generate properties.
     * @param {import("./types").RPCMessage} toMsg The message ACK should be created for.
     * @param {import("./types").RPCMessage} props For overriding properties of ACK message.
     * @param {Object} opts Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createACKMessage(toMsg, props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "ack",
            lane: "sys",
            correlatesTo: toMsg.id,
            budgetMs: this.settings.protocol.ackWaitMs,
        }, opts);

        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createStateUpdateMessage(toMsg, props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "stateUpdate",
            correlatesTo: toMsg.id,
            lane: toMsg.lane,
        }, opts);
        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createSubscribeMessage(props = {}, opts = {}) {
        if(!props.route) throw new Error("Request RPC message must have a route!");

        if(!opts) opts = {};
        if(!opts.priority) opts.priority = "low";
        const {origin, ...optsWithoutOrigin} = opts;
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "subscribe",
            op: "none",
            budgetMs: this.#pickBudgetMs(opts),
            payload: {...optsWithoutOrigin},
        }, opts);
        if(origin) message.origin = origin;

        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createCancelMessage(props = {}, opts = {}) {
        if(!props.correlatesTo) throw new Error("Request RPC message must have a correlatesTo!");

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "cancel",
            lane: "sys",
            budgetMs: this.settings.protocol.ackWaitMs,
        }, opts);

        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createUnsubscribeMessage(props = {}, opts = {}) {
        if(!props.correlatesTo) throw new Error("Request RPC message must have a correlatesTo!");

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "unsubscribe",
            lane: "sys",
            budgetMs: this.settings.protocol.ackWaitMs,
        }, opts);

        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createHelloMessage(props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "hello",
            lane: "sys",
            budgetMs: this.settings.protocol.ackWaitMs,
        }, opts);
        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createReplyMessage(toMsg, props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "reply",
            correlatesTo: toMsg.id,
            lane: toMsg.lane,
        }, opts);

        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createRequestMessage(props = {}, opts = {}) {
        //console.log("createRequestMessage", props);
        if(!props.route) throw new Error("Request RPC message must have a route!");

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "request",
            op: "call",
            budgetMs: this.#pickBudgetMs(opts),
        }, opts);
        message.idempotencyKey = opts?.idempotencyKey ?? message.id;
        // TODO: Where does the signal resides again?
        if(opts?.signal) opts.signal.addEventListener("abort", () => {
            if(message.origin) {
                this.cancel(message.id, {origin: message.origin});
            } else {
                this.cancel(message.id);
            }
        }, { once: true });
        
        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createEmitMessage(props = {}, opts = {}) {
        if(!props.route) throw new Error("Emit RPC message must have a route!");
        
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "emit",
            op: "event",
            budgetMs: this.#pickBudgetMs(opts),
        }, opts);
        message.idempotencyKey = opts?.idempotencyKey ?? message.id;
        
        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createHeartbeatMessage(props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "heartbeat",
        }, opts);

        return message;
    }

    /**
     * @returns {import("./types").RPCMessage}
     */
    #createErrorMessage(toMsg, props = {}, opts = {}) {
        const errorPayload = props.payload ?? {};
        if(props.code) { errorPayload.code = props.code; delete props.code; }
        if(props.message) { errorPayload.message = props.message; delete props.message; }
        if(props.err) { errorPayload.err = props.err; delete props.err; }
        if(props.replyable) { errorPayload.replyable = props.replyable; delete props.replyable; }

        if(!errorPayload.code) throw new Error("code or payload.code is required for error message");
        if(!errorPayload.message) errorPayload.message = "";
        if(!errorPayload.replyable) errorPayload.replyable = false;

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: "0.1",
            type: "error",
            lane: "sys",
            correlatesTo: toMsg.id,
            payload: errorPayload,
        }, opts);

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props={}] 
     * @returns {import("./types").RPCMessage}
     */
    #createRPCMessage(props={}, opts={}) {
        //console.log("createRPCMessage", props);
        /** @type {import("./types").RPCMessage} */
        const message = {
            ...props,
            id: uuidv7(),
            ts: this.#now(),
            gen: this.#generation,
        };
        if(opts?.origin) message.origin = opts.origin;
        
        // We could be able to get lane from route
        if(message.route && !message.lane) {
            message.lane = this.#laneKey(message.route, opts?.priority);
        }
        message.lane = message.lane ?? "noLaneSet";

        // "sys" lane is meant for immediate communication like ACK or unsubscribe; no lane sequence is assumed
        if(message.lane === "sys" && message.seq) delete message.seq;
        if(message.lane && message.lane !== "sys") message.seq = this.#nextSeq(message.lane);

        if(!message.payload) message.payload = {};

        //console.log("createRPCMessage =>", message);
        return message;
    }

    #startHeartbeat() {
        const period = Math.max(1000, this.settings.protocol.heartbeatMs || 5000);
        this.#stopHeartbeat();
        this.heartbeatTimer = setInterval(() => {
            if(this.webSocket && this.webSocket.readyState === WebSocket.OPEN) {
                // const lane = "sys";
                // this.webSocket.send(JSON.stringify({
                //     v: "0.1",
                //     type: "heartbeat",
                //     id: uuidv7(),
                //     lane: lane,
                //     ts: this.#now(),
                //     seq: this.#nextSeq(lane),
                // }));
                this.#sendRaw(this.#createRPCMessage({
                    v: "0.1",
                    type: "heartbeat",
                    lane: "sys",
                }));
            }
        }, period);

        const awolCap = Math.max(period * 3, 10000);
        this.awolTimer = setInterval(() => {
            if(!this.webSocket) return;
            if(this.webSocket.readyState === WebSocket.CLOSED || this.webSocket.readyState === WebSocket.CLOSING) return;
            // If open but we stalled, rely on server-side idle timout
        }, awolCap);
    }

    #stopHeartbeat() {
        if(this.heartbeatTimer) clearInterval(this.heartbeatTimer);
        if(this.awolTimer) clearInterval(this.awolTimer);
        this.heartbeatTimer = null;
        this.awolTimer = null;
    }

    #tickHeartbeat() {
        if(!this.webSocket || this.webSocket.readyState !== WebSocket.OPEN) return;
        
        try {
            this.#sendRaw(this.#createHeartbeatMessage());
        } catch { /* Ignore errors */ }

        // Loss of connection detection - if more than 3 times interval, close socket.
        const awolMs = this.heartbeat.intervalMs * 3;
        if(Date.now() - this.heartbeat.lastSeen > awolMs) {
            try { this.webSocket.close(); } catch { /* ignore */ }
        }
    }

    #sendRaw(obj) {
        if(this.webSocket && this.webSocket.readyState === WebSocket.OPEN) {
            let str = JSON.stringify(obj);
            this.webSocket.send(str);
            this.#logOutgoingStr(str);
        }
    }
    
    #laneKey(route, prio) { return route?.capability ? `cap:${route.capability}` : route?.object ? `obj:${route.object}` : "sys"; }

    #nextSeq(lane) {
        const laneState = this.laneStates.get(lane) || { nextSeq: 1, peerAck: 0, inFlight: 0 };
        this.laneStates.set(lane, laneState);
        return (laneState.nextSeq++);
    }

    #pickBudgetMs(opts) { return opts?.budgetMs ?? this.#resolveClassCfg(opts).serviceTtlMs; }

    #resolveClassCfg(opts) {
        const cls = opts?.class || "request.medium";
        const cfg = (this.settings?.timeouts?.classes?.[cls]) || { serviceTtlMs: 3000, clientPatienceExtraMs: 200 };
        return cfg;
    }

    /**
     * @param {uuidv7} id 
     * @param {import("./types").Invocation} invocation 
     * @returns {import("./types").Subscription}
     */
    #makeSub(id, invocation, opts) {
        const self = this;
        const listeners = {};
        const sub = {
            on(event, fn) { listeners[event] = fn; },
            _emit(event, data) { listeners[event]?.(data); },
            close: () => { self.unsubscribe(id); },
        };
        Object.defineProperty(sub, "id", {
            value: id,
            writable: false,
            configurable: false,
            enumerable: true,
        });
        Object.defineProperty(sub, "invocation", {
            value: invocation,
            writable: false,
            configurable: false,
            enumerable: true,
        });
        Object.defineProperty(sub, "opts", {
            value: opts,
            writable: false,
            configurable: false,
            enumerable: true,
        });
        return sub;
    }

    async #open() {
        await this.#connectOnce();
        this.#startHeartbeat();
        this.#flushQueue();
    }

    /**
     * Register a frontend capability the backend can call.
     * 
     * @param {string} capability - The name of the capability.
     * @param {CapabilityHandlers} handlers - An object with methods to handle calls, emits and subscriptions.
     */
    expose(capability, handlers) {
        console.log(`exposing capability "${capability}"`);
        if(this.localCaps.has(capability)) {
            throw new Error(`Capability already exposed: '${capability}'.`);
        }
        this.localCaps.set(capability, handlers);
        return () => this.localCaps.delete(capability);
    }
}

export function defaultSettings() {
    console.warn("Loading default settings - this shouldn't happen if everything is set up correctly!")
    return {
        loadedFromFrontendDefaults: true,
        protocol: {ackWaitMs: 250, graceWindowMs: 150, maxInFlightPerLane: 64, heartbeatMs: 5000},
        timeouts: {classes: {
            "request.fast": {serviceTtlMs: 800, clientPatienceExtraMs: 150},
            "request.medium": {serviceTtlMs: 3000, clientPatienceExtraMs: 200},
            "request.heavy": {serviceTtlMs: 30000, clientPatienceExtraMs: 250}},
        },
        streams: {default: {targetHz: 10, maxQueueMs: 200, coalesce: "drop-oldest"}},
        http: {retry: 2,backoff: {baseMs: 250, maxMs: 1000, jitterPct: 30}, timeoutCapMs: 30000},
        mods: {allowSymlinks: false},
        httpProxy: {
            allowList: ["httpbin.org", "api.openai.com", "localhost", "127.0.0.1"],
            buckets: {default: {rpm: 600, burst: 200}},
        },
        debug: {
            backend:  {rpc: {incomingMessages: {log: false, ignoreTypes: ["ack", "heartbeat"]},
                             outgoingMessages: {log: false, ignoreTypes: ["ack", "heartbeat"]}}},
            frontend: {rpc: {incomingMessages: {log: false, ignoreTypes: ["ack", "heartbeat"]},
                             outgoingMessages: {log: false, ignoreTypes: ["ack", "heartbeat"]}}},
        },
    };
}

