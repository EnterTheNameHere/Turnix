// frontend/assets/rpc-client.js

import { uuidv7 } from 'uuidv7';

/**
 * Strict, portable ops with no type coercion.
 * 
 * 'equals':        left === right
 * 'notEquals':     left !== right
 * 'in':            right is array and contains left
 * 'notIn':         right is array and does not contain left
 * 'exists':        left is not undefined
 * 'notExists':     left is undefined
 * 'lt':            left < right
 * 'lte':           left <= right
 * 'gt':            left > right
 * 'gte':           left >= right
 * 'matches':       RegExp(right).test(left)
 * 
 * @param {unknown} left First value to compare, or check if it exists, or if it's in `right`, or matches against RegExp
 * @param {'equals'|'notEquals'|'in'|'notIn'|'exists'|'notExists'|'lt'|'lte'|'gt'|'gte'|'matches'} op The evaluating operator
 * @param {unknown} right Second value to compare against, being the array, or being RegExp pattern
 * @return {boolean}
 */
export function evaluateOp(left, op, right) {
    switch(op) {
        case 'equals':      return left === right;
        case 'notEquals':   return left !== right;
        case 'in':          return Array.isArray(right) && right.some(val => val === left);
        case 'notIn':       return Array.isArray(right) && !right.some(val => val === left);
        case 'exists':      return left !== undefined;
        case 'notExists':   return left === undefined;
        case 'lt':          return typeof left === 'number' && typeof right === 'number' && left < right;
        case 'lte':         return typeof left === 'number' && typeof right === 'number' && left <= right;
        case 'gt':          return typeof left === 'number' && typeof right === 'number' && left > right;
        case 'gte':         return typeof left === 'number' && typeof right === 'number' && left >= right;
        case 'matches':     {
            if(typeof left !== 'string' || typeof right !== 'string') return false;
            let pattern = right, flags = '';
            // Protect against ReDoS
            if(pattern.length > 2000) return false;
            const mm = /^\/(.+)\/([a-z]*)$/u.exec(right);
            if(mm) { pattern = mm[1]; flags = mm[2]; }
            try { return new RegExp(pattern, flags).test(left); }
            catch { return false; } // Treat invalid regex as no match
        }
        default:            return false;
    }
}

/**
 * Returns value of property accessed using `path` string.
 * - Accepts 'a.b.c' or 'a/b/c'.
 * 
 * @param {object} obj Object which properties to traverse.
 * @param {string} path Path to traverse.
 * @return {unknown} value found, or undefined.
 */
export function getByPath(obj, path) {
    if(!obj || !path) return undefined;

    const parts = [];
    let curr = '', esc = false;
    // Make sure to filter out escapes
    for(const ch of path) {
        if(esc) { curr += ch; esc = false; continue; }
        if(ch === '\\') { esc = true; continue; }
        if(ch === '.' || ch === '/') {
            if(curr) parts.push(curr), curr = ''; continue;
        }
        curr += ch;
    }
    if(curr) parts.push(curr);

    let val = obj;
    for(const part of parts) {
        if(val == null || typeof val !== 'object' || !(part in val)) return undefined;
        val = val[part];
    }
    return val;
}

/**
 * Normalize input (string or object) to a message object.
 * @param {string|import("./types").RPCMessage} rpcMessageOrString
 */
export function normalizeMessage(rpcMessageOrString) {
    if(typeof rpcMessageOrString === 'string') {
        const text = rpcMessageOrString.trimStart();
        if(!text) return null;

        // Quickly check if text starts as valid JSON value.
        const first = text[0];
        // {} [] "" true false null (negative/positive) digit
        if('{["tfn-+0123456789'.includes(first)) {
            try { return JSON.parse(text); }
            catch { return null; } // Not a valid JSON
        }

        // Message is not a JSON
        return null;
    }

    if(rpcMessageOrString && typeof rpcMessageOrString === 'object') {
        return rpcMessageOrString;
    }

    // We expect JSON or object and what we got is neither of that.
    return null;
}

/**
 * Decide if we should log.
 * @param {import("./types").RPCMessage} msg
 * @param {object} cfg
 * @returns {boolean} 
 */
export function shouldLogRPCMessage(msg, cfg) {
    const conf = cfg ?? {log: false};
    if(conf.log === false) return false;

    // Ignore by type
    const msgType = msg?.type;
    // TODO: warn ignored types is not array or is empty (which seems weird, as ack and heartbeat are spammy)
    if(Array.isArray(conf.ignoreTypes) && msgType && conf.ignoreTypes.includes(msgType)) {
        // Type is in ignore list
        return false;
    }

    // Match by type rule
    const rules = Array.isArray(conf.rules) ? conf.rules : [];
    const rule = rules.find((rl) => rl.type === msgType || rl.type === '*');

    if(!rule) {
        // No type rule => fallback to global log, which by this time is true, so log message...
        return true;
    }

    // Evaluate tests in order; first match wins
    const tests = Array.isArray(rule.tests) ? rule.tests : [];
    for(const test of tests) {
        const left = msg ? getByPath(msg, test.property) : undefined;
        // TODO: warn if test doesn't have correctly defined values - property, op, value, and whether to log
        if(evaluateOp(left, test.op, test.value)) {
            return test.shouldLog ?? true;
        }
    }

    // No test matched => use capability default
    // TODO: warn that category rule doesn't have whether it should log or not
    return rule.shouldLog ?? false;
}

/**
 * @template {any[]} A
 * @returns {{
 *     add(fn: (...args: A) => void): { unsubscribe(): void, fn: (...args: A) => void };
 *     once(fn: (...args: A) => void): { unsubscribe(): void, fn: (...args: A) => void };
 *     remove(target: { unsubscribe(): void, fn: (...args: A) => void } | ((...args: A) => void)): void;
 *     emit(...args: A): void;
 *     size(): number;
 * }}
 */
export function createEmitter() {
    const listeners = new Set();
    const byOriginal = new Map();

    const add = (fn) => {
        if(typeof fn !== 'function') throw new TypeError('Event handler must be a function');
        const wrapped = (...args) => fn(...args);

        listeners.add(wrapped);
        let set = byOriginal.get(fn);
        if(!set) byOriginal.set(fn, (set = new Set()));
        set.add(wrapped);

        const handle = {
            fn,
            unsubscribe() {
                const s = byOriginal.get(fn);
                if(s) {
                    s.delete(wrapped);
                    if(s.size === 0) byOriginal.delete(fn);
                }
                listeners.delete(wrapped);
            },
        };
        return handle;
    };

    const once = (fn) => {
        if(typeof fn !== 'function') throw new TypeError('Event handler must be a function');
        let handle = null;
        const wrapped = (...args) => {
            // Call and then unsubscribe
            try { fn(...args); } finally { handle && handle.unsubscribe(); }
        };
        // Register using the original fn as the key so remove(fn) still works
        listeners.add(wrapped);
        let set = byOriginal.get(fn);
        if(!set) byOriginal.set(fn, (set = new Set()));
        set.add(wrapped);

        handle = {
            fn,
            unsubscribe() {
                const s = byOriginal.get(fn);
                if(s) {
                    s.delete(wrapped);
                    if(s.size === 0) byOriginal.delete(fn);
                }
                listeners.delete(wrapped);
            },
        };
        return handle;
    };

    const remove = (target) => {
        if(!target) return;
        if(typeof target === 'function') {
            // Remove all wrappers registered for this original function
            const set = byOriginal.get(target);
            if(set) {
                for(const w of set) listeners.delete(w);
                byOriginal.delete(target);
            }
            return;
        }
        if(typeof target.unsubscribe === 'function') {
            target.unsubscribe();
            return;
        }
        throw new TypeError('remove expects a function or handle with unsubscribe()');
    };

    const emit = (...args) => {
        // Snapshot to avoid mutation during iteration
        for(const fn of [...listeners]) fn(...args);
    };

    const size = () => {
        return listeners.size;
    };

    return {
        add, once, remove, emit, size,
    };
}

// Assignment sugar
export function defineEventProperty(obj, propName, emitter) {
    Object.defineProperty(obj, propName, {
        enumerable: true,
        get() { return emitter; }, // Returns read only emitter with .add/.once/.remove/.emit/.size on get.
        set(value) { // On property set it adds handler to emitter; prevents overriding of the emitter.
            if(typeof value !== 'function') throw new TypeError(`${propName} event emitter setter expects a function`);
            emitter.add(value); // Register and forget.
        },
    });
}

export class PerLaneSerializer {
    constructor() {
        /** @type {Map<string, Promise<void>>} */
        this.tails = new Map();
    }

    enqueue(lane, task) {
        const tail = this.tails.get(lane) || Promise.resolve();
        const chained = tail.then(() => task()).catch(err => {
            console.error('[lane]', lane, err);
        });
        this.tails.set(lane, chained.finally(() => {
            if(this.tails.get(lane) === chained) {
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

    /** @type {boolean} */
    #ready = false; // Is websocket rpc ready for use?
    /** @type {import("./types").Gen|null} */
    #generation = null; // Connection generation epoch
    #lastWelcome = null;
    #welcomeDeferred = null;

    // TODO: Send settings if updated to backend, and receive settings from backend
    /**
     * @param {string} url
     * @param {ReturnType<typeof defaultSettings>|any} settings
     * @param {string|undefined} token
     */
    constructor(url, settings, token) {
        this.url = url;
        this.token = token;
        this.settings = settings || defaultSettings();
        
        /** @type {WebSocket|null} */
        this.webSocket = null;
        this.connected = false;
        this.reconnectAttempts = 0;
        /** @type {import("./types").Gen|null} */
        this.#generation = null;
        
        this.offlineQueue = [];          // Used to collect messages when connection is not OPEN; sent once connection is OPEN
        /** @type {Map<string, {nextSeq:number, peerAck?:number, inFlight:number}>} */
        this.laneStates = new Map();
        /** @type {Map<string, {resolve:Function, reject:Function, timer:any, laneKey?:string, wantAck?:boolean, onFinally?:Function}>} */
        this.pending = new Map();        // id -> { resolve, reject, timer, opts }
        /** @type {Map<string, import("./types").Subscription>} */
        this.subscriptions = new Map();  // subId -> subscription object
        this.localCaps = new Map();      // capability -> { call?, emit?, subscribe? }

        this.heartbeatTimer = null;
        this.awolTimer = null;
        this.heartbeat = { timer: null, intervalMs: this.settings?.protocol?.heartbeatMs ?? 5000, lastSeen: Date.now() };
        
        // To prevent race for "welcome"
        this.#welcomeDeferred = this.#newDeferred();

        // Frontend events
        /** @type {ReturnType<typeof createEmitter>} */
        this.onReadyChange = createEmitter();
        defineEventProperty(this, 'onReadyChange', this.onReadyChange);
        /** @type {ReturnType<typeof createEmitter>} */
        this.onWelcome = createEmitter();
        defineEventProperty(this, 'onWelcome', this.onWelcome);
        /** @type {ReturnType<typeof createEmitter>} */
        this.onClientReady = createEmitter();
        defineEventProperty(this, 'onClientReady', this.onClientReady);
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
    }

    #onClose() {
        this.#setReady(false);
        this.connected = false;
        this.#stopHeartbeat();

        for(const [, pend] of this.pending.entries()) {
            clearTimeout(pend.timer);
            pend.reject(new Error('DISCONNECTED'));
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

    /**
     * @param {Object} p0
     * @param {import("./types").Route} p0.route 
     * @param {string} p0.path 
     * @param {string} p0.op 
     * @param {unknown[]} p0.args
     * @param {import("./types").Payload} [payload={}]
     * @param {{}} [opts={}] 
     */
    request({route, path, op, args}, payload = {}, opts = {}) {
        const msg = this.#createRequestMessage({route, path, op, args, payload}, opts);
        return this.#sendWithReply(msg, msg.lane, opts);
    }

    /**
     * @param {import("./types").Route} route
     * @param {string} path
     * @param {import("./types").Payload} [payload={}]
     * @param {{noAck?:boolean}} [opts={}]
     * @return {Promise<boolean>}
     */
    emit(route, path, payload = {}, opts = {}) {
        const msg = this.#createEmitMessage({ route, path, payload }, opts);
        if(opts.noAck) {
            this.#enqueue(msg);
            return Promise.resolve(true);
        }
        return this.#sendWithAck(msg);
    }

    /**
     * @param {import("./types").Route} route
     * @param {string} path
     * @param {string} op
     * @param {{noAck?:boolean}} [opts={}]
     * @return {Promise<any>}
     */
    async subscribe(route, path, op, opts = {}) {
        const msg = this.#createSubscribeMessage({route, path, op}, opts);
        const subscription = this.#makeSub(msg.id, {route, path, op}, opts);
        this.subscriptions.set(msg.id, subscription);
        await this.#sendWithAck(msg);
        return subscription;
    }

    unsubscribe(subId, opts = {}) {
        const msg = this.#createUnsubscribeMessage({correlatesTo: subId}, opts);
        this.#enqueue(msg);
        this.subscriptions.delete(subId);
    }

    cancel(correlatesTo, opts = {}) {
        const msg = this.#createCancelMessage({correlatesTo}, opts);
        this.#enqueue(msg);
    }

    async clientReady({ loaded = [], failed = [], modsHash = null } = {}) {
        const msg = this.#createClientReadyMessage({
            payload: {loaded, failed, modsHash}
        });
        await this.#sendWithAck(msg);
        this.onClientReady.emit();
    }

    #setReady(value) {
        if(this.#ready === value) return;
        this.#ready = value;
        this.onReadyChange.emit(value);
    }

    #onMessage(ev) {
        /** @type {import("./types").RPCMessage|null} */
        let msg;
        try {
            msg = JSON.parse(ev.data);
            this.#logIncomingStr(msg);
        } catch(err) {
            this.#logIncomingStr(ev.data);
            console.error('[RPC] Error parsing message received.', err, ev);
            return;
        }

        // Bump heartbeat
        this.heartbeat.lastSeen = Date.now();

        // ----- Handshake -----
        if(msg.type === 'welcome') {
            this.#generation = msg.gen;
            this.#setReady(true);
            this.#flushQueue();

            let welcomeState = msg.payload?.state ?? null;
            this.#lastWelcome = welcomeState;
            this.#welcomeDeferred.resolve(welcomeState);
            // Prepare a new deferred for the *next* welcome (e.g., after reconnect)
            this.#welcomeDeferred = this.#newDeferred();

            // Resume subscriptions (best effort)
            for(const [oldSubId, subscription] of [...this.subscriptions.entries()]) {
                // Create a new subscribe with a new id, but keep the local sub object
                const newMsg = this.#createSubscribeMessage({
                    route: subscription.invocation.route,
                    op: subscription.invocation.op ?? 'none',
                    path: subscription.invocation.path,
                    payload: { ...subscription.opts },
                }, subscription.opts);

                // Move the sub to the new id
                this.subscriptions.delete(oldSubId);
                this.subscriptions.set(newMsg.id, subscription);
                
                // Update exposed id so .close() unsubscribes correctly
                try { subscription.id = newMsg.id; } catch {/* ignored */}

                // Send and wait only for ACK so we don't stall whole connection
                this.#sendWithAck(newMsg).catch(() => {});
            }
            this.onWelcome.emit(welcomeState);
        }

        // Drop stale messages
        if(msg.gen && this.#generation && (msg.gen.num !== this.#generation.num || msg.gen.salt !== this.#generation.salt)) {
            return;
        }

        // Frontend routing: backend is calling our exposed capability
        if(['request', 'emit', 'subscribe'].includes(msg.type) && msg.route?.capability) {
            const cap = this.localCaps.get(msg.route.capability);
            if(cap) {
                // Ack immediately
                this.#enqueue(this.#createACKMessage(msg));

                // For a request, we want to execute a call()
                if(msg.type === 'request' && typeof cap.call === 'function') {
                    Promise.resolve()
                        .then(() => cap.call(msg.path, msg.args || [], { origin: msg.origin, id: msg.id }))
                        .then((payload) => {
                            const message = this.#createReplyMessage(msg, {payload: payload});
                            this.#enqueue(message);
                            return;
                        })
                        .catch((err) => {
                            const message = this.#createErrorMessage(msg, {
                                code: 'FRONTEND_ERROR',
                                message: String(err),
                                err: err,
                                retryable: false,
                            });
                            this.#enqueue(message);
                        });
                    return;
                }
                // For an emit, we want to execute an emit()
                else if(msg.type === 'emit' && typeof cap.emit === 'function') {
                    try {
                        cap.emit(msg.path, msg.payload, { origin: msg.origin, id: msg.id });
                    } catch(err) {
                        // Emit is non reporting, so just log it
                        console.warn(`Error caught when executing capability '${msg.path}' emit: ${err?.message}`, err, msg);
                    }
                    return;
                }
                // For a subscribe, we want to execute a subscribe()
                else if(msg.type === 'subscribe' && typeof cap.subscribe === 'function') {
                    try {
                        const push = async (payload) => {
                            const message = this.#createStateUpdateMessage(msg, {payload});
                            this.#enqueue(message);
                        };
                        const abortController = new AbortController();
                        /** @type {import("./types").SubscribeCtx} */
                        const ctx = {id: msg.id, origin: msg.origin, signal: abortController.signal, push};

                        Promise.resolve()
                            .then(() => cap.subscribe(msg.path, msg.payload ?? {}, ctx))
                            .then((stream) => {
                                let subscription = this.subscriptions.get(msg.id);
                                if(!subscription) {
                                    subscription = this.#makeSub(msg.id, {route: msg.route, path: msg.path, op: msg.op}, {});
                                    this.subscriptions.set(msg.id, subscription);
                                }

                                subscription._jsOnCancel = stream?.onCancel ?? null;

                                // If handler didn't provide its own push, use our
                                if(stream && typeof stream.push !== 'function') {
                                    // Non-enumerable to prevent JSON serialization
                                    Object.defineProperty(stream, 'push', {value: push, enumerable: false});
                                }

                                // Send initial data if provided
                                if(stream?.initial !== undefined) push(stream.initial);
                                return;
                            })
                            .catch((err) => {
                                this.#enqueue(this.#createErrorMessage(msg, {
                                    code: 'FRONTEND_SUBSCRIBE_ERROR',
                                    message: String(err),
                                    err: err,
                                    retryable: false,
                                }));
                            });
                    } catch(err) {
                        this.#enqueue(this.#createErrorMessage(msg, {
                            code: 'FRONTEND_SUBSCRIBE_ERROR',
                            message: String(err),
                            err: err,
                            retryable: false,
                        }));
                    }
                    return;
                }
                
                // If capability exists but missing specific handler (e.g., request), report cleanly
                else if(msg.type === 'request' && !cap.call) {
                    this.#enqueue(this.#createErrorMessage(msg, {
                        code: 'FRONTEND_REQUEST_ERROR',
                        message: `Capability '${msg.route.capability}' does not have 'call' method`,
                        retryable: false,
                    }));
                    return;
                }
                else if(msg.type === 'emit' && !cap.emit) {
                    this.#enqueue(this.#createErrorMessage(msg, {
                        code: 'FRONTEND_EMIT_ERROR',
                        message: `Capability '${msg.route.capability}' does not have 'emit' method`,
                        retryable: false,
                    }));
                    return;
                }
                else if(msg.type === 'subscribe' && !cap.subscribe) {
                    this.#enqueue(this.#createErrorMessage(msg, {
                        code: 'FRONTEND_SUBSCRIBE_ERROR',
                        message: `Capability '${msg.route.capability}' does not have 'subscribe' method`,
                        retryable: false,
                    }));
                    return;
                }
            }
        }

        // Auto ack for non-control
        if(!['ack', 'welcome', 'hello', 'heartbeat'].includes(msg.type)) {
            this.#enqueue(this.#createACKMessage(msg));
        }

        if(msg.type === 'reply' || msg.type === 'error') {
            const pending = this.pending.get(msg.correlatesTo);
            if(pending) {
                clearTimeout(pending.timer);
                this.pending.delete(msg.correlatesTo);
                try {
                    if(msg.type === 'error') pending.reject(new Error(msg.payload?.message || 'ERROR'));
                    else pending.resolve(msg.payload);
                } finally {
                    pending.onFinally?.();
                }
                return;
            }
            const sub = this.subscriptions.get(msg.correlatesTo);
            if(sub) {
                if(msg.type === 'reply') {
                    sub?._emit('reply', msg.payload);
                } else if(msg.type == 'error') {
                    sub?._emit('error', msg.payload);
                }
                return;
            }
            return;
        }

        if(msg.type === 'stateUpdate') {
            const sub = this.subscriptions.get(msg.correlatesTo);
            sub?._emit('update', msg.payload);
            return;
        }

        if(msg.type === 'ack') {
            const pending = this.pending.get(msg.correlatesTo);
            if(pending && pending.wantAck) {
                clearTimeout(pending.timer);
                pending.resolve(true);
                this.pending.delete(msg.correlatesTo);
                pending.onFinally?.();
            }
            return;
        }

        if(msg.type === 'cancel' || msg.type === 'unsubscribe') {
            const subscription = this.subscriptions.get(msg.correlatesTo);
            if(subscription && typeof subscription._jsOnCancel === 'function') {
                try { subscription._jsOnCancel(msg); }
                catch(err) { console.warn(`Error caught during subscription cancel: ${err?.message}`, err, msg); }
            }
            this.subscriptions.delete(msg.correlatesTo);
            return;
        }
    }

    /**
     * @param {import("./types").RPCMessage} msg
     * @param {string} lane
     * @param {object} opts
     */
    #sendWithReply(msg, lane, opts) {
        const classCfg = this.#resolveClassCfg(opts);
        const waitMs = Math.min(
            (msg.budgetMs ?? classCfg.serviceTtlMs) + (classCfg.clientPatienceExtraMs ?? 200),
            (this.settings?.http?.timeoutCapMs ?? 30000)
        );

        return new Promise((resolve, reject) => {
            const entry = {
                resolve,
                reject,
                timer: null,
                laneKey: lane,
                wantAck: false,
                onFinally: () => this.#onLaneDone(lane),
            };
            
            this.pending.set(msg.id, entry);

            entry.timer = setTimeout(() => {
                this.pending.delete(msg.id);
                entry.onFinally?.();
                // Best effort cancel on the wire
                if(msg?.origin) this.cancel(msg.id, {origin: msg.origin});
                else this.cancel(msg.id);
                reject(new Error('TIMEOUT'));
            }, waitMs);

            this.#scheduleSend(msg, () => this.#enqueue(msg));
        });
    }

    /**
     * @param {import("./types").RPCMessage} msg
     */
    #sendWithAck(msg) {
        return new Promise((resolve, reject) => {
            const ackWait = this.settings?.protocol?.ackWaitMs ?? 250;
            const entry = {
                resolve,
                reject,
                timer: null,
                laneKey: msg.lane,
                wantAck: true,
                onFinally: () => this.#onLaneDone(msg.lane),
            };

            this.pending.set(msg.id, entry);

            entry.timer = setTimeout(() => {
                this.pending.delete(msg.id);
                entry.onFinally?.();
                reject(new Error('NO_ACK'));
            }, ackWait);
            
            this.#scheduleSend(msg, () => this.#enqueue(msg));
        });
    }

    #logOutgoingStr(rpcMessageOrStr) {
        const cfg = this.settings?.debug?.frontend?.rpc?.outgoingMessages ?? {log: false};
        if(!cfg?.log) return;
        
        const normalizedMessage = normalizeMessage(rpcMessageOrStr);
        const wantToLog = shouldLogRPCMessage(normalizedMessage, cfg);
        if(!wantToLog) return;
        console.log('[RPC] sending:', normalizedMessage ?? rpcMessageOrStr);
    }

    #logIncomingStr(rpcMessageOrStr) {
        const cfg = this.settings?.debug?.frontend?.rpc?.incomingMessages ?? {log: false};
        if(!cfg?.log) return;
        const normalizedMessage = normalizeMessage(rpcMessageOrStr);
        const wantToLog = shouldLogRPCMessage(normalizedMessage, cfg);
        if(!wantToLog) return;
        console.log('[RPC] incoming:', normalizedMessage ?? rpcMessageOrStr);
    }

    #enqueue(obj) {
        if(this.webSocket && this.webSocket.readyState === WebSocket.OPEN) {
            this.#sendRaw(obj);
        } else {
            const maxOfflineQueue = this.settings?.protocol?.maxOfflineQueue ?? 2000;
            if(this.offlineQueue.length >= maxOfflineQueue) this.offlineQueue.shift();
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
     * @param {import("./types").RPCMessage} toMsg The RPCMessage ACK should be created for.
     * @param {import("./types").RPCMessage} [props] For overriding properties of ACK RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createACKMessage(toMsg, props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'ack',
            lane: 'sys',
            correlatesTo: toMsg.id,
            budgetMs: this.settings?.protocol?.ackWaitMs ?? 250,
        }, opts);

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} toMsg The original RPCMessage containing id.
     * @param {import("./types").RPCMessage} [props] For overriding properties of stateUpdate RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createStateUpdateMessage(toMsg, props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'stateUpdate',
            correlatesTo: toMsg.id,
            lane: toMsg.lane,
        }, opts);
        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of subscribe RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createSubscribeMessage(props = {}, opts = {}) {
        if(!props.route) throw new Error('Request RPC message must have a route!');
        const options = (opts && typeof opts === 'object') ? opts : {};
        if(!options.priority) options.priority = 'low';
        const {origin, ...optsWithoutOrigin} = options;

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'subscribe',
            budgetMs: this.#pickBudgetMs(options),
            payload: {...optsWithoutOrigin},
        }, options);
        if(origin) message.origin = origin;

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of cancel RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createCancelMessage(props = {}, opts = {}) {
        if(!props.correlatesTo) throw new Error('Request RPC message must have a correlatesTo!');

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'cancel',
            lane: 'sys',
            budgetMs: this.settings?.protocol?.ackWaitMs ?? 250,
        }, opts);

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of unsubscribe RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createUnsubscribeMessage(props = {}, opts = {}) {
        if(!props.correlatesTo) throw new Error('Request RPC message must have a correlatesTo!');

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'unsubscribe',
            lane: 'sys',
            budgetMs: this.settings?.protocol?.ackWaitMs ?? 250,
        }, opts);

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of hello RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createHelloMessage(props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'hello',
            lane: 'sys',
            budgetMs: this.settings?.protocol?.ackWaitMs ?? 250,
            gen: {num: -1, salt: 'salt'},
        }, opts);
        return message;
    }

    /**
     * @param {import("./types").RPCMessage} toMsg The original RPCMessage containing id.
     * @param {import("./types").RPCMessage} [props] For properties of reply RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createReplyMessage(toMsg, props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'reply',
            correlatesTo: toMsg.id,
            lane: toMsg.lane,
        }, opts);

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of request RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createRequestMessage(props = {}, opts = {}) {
        if(!props.route) throw new Error('Request RPC message must have a route!');

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'request',
            op: 'call',
            budgetMs: this.#pickBudgetMs(opts),
        }, opts);
        message.idempotencyKey = opts?.idempotencyKey ?? message.id;
        // TODO: Where does the signal resides again?
        if(opts?.signal) opts.signal.addEventListener('abort', () => {
            if(message.origin) this.cancel(message.id, {origin: message.origin});
            else this.cancel(message.id);
        }, {once: true});
        
        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of emit RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createEmitMessage(props = {}, opts = {}) {
        if(!props.route) throw new Error('Emit RPC message must have a route!');
        
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'emit',
            op: 'event',
            budgetMs: this.#pickBudgetMs(opts),
        }, opts);
        message.idempotencyKey = opts?.idempotencyKey ?? message.id;
        
        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of clientReady RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createClientReadyMessage(props = {}, opts = {}) {
        const _props = {...props};
        // Empty arrays are allowed
        const loaded = Array.isArray(_props?.payload?.loaded ?? _props?.loaded) ? (_props?.payload?.loaded ?? _props?.loaded) : [];
        const failed = Array.isArray(_props?.payload?.failed ?? _props?.failed) ? (_props?.payload?.failed ?? _props?.failed) : [];
        const modsHash = _props?.payload?.modsHash ?? _props?.modsHash;

        // Clean possible top level mirrors
        delete _props.loaded;
        delete _props.failed;
        delete _props.modsHash;

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            v: '0.1',
            ..._props,
            type: 'clientReady',
            lane: 'sys',
            payload: {loaded, failed, modsHash},
        }, opts);

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of heartbeat RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createHeartbeatMessage(props = {}, opts = {}) {
        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'heartbeat',
            lane: 'sys',
        }, opts);

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} toMsg The original RPCMessage containing id.
     * @param {import("./types").RPCMessage} [props] For properties of error RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createErrorMessage(toMsg, props = {}, opts = {}) {
        const errorPayload = props.payload ?? {};
        if(props.code) { errorPayload.code = props.code; delete props.code; }
        if(props.message) { errorPayload.message = props.message; delete props.message; }
        if(props.err) { errorPayload.err = props.err; delete props.err; }
        if(props.retryable) { errorPayload.retryable = props.retryable; delete props.retryable; }

        if(!errorPayload.code) throw new Error('code or payload.code is required for error message');
        if(!errorPayload.message) errorPayload.message = '';
        if(!errorPayload.retryable) errorPayload.retryable = false;

        /** @type {import("./types").RPCMessage} */
        const message = this.#createRPCMessage({
            ...props,
            v: '0.1',
            type: 'error',
            lane: 'sys',
            correlatesTo: toMsg.id,
            payload: errorPayload,
        }, opts);

        return message;
    }

    /**
     * @param {import("./types").RPCMessage} [props] For properties of RPCMessage.
     * @param {Object} [opts] Additional options which can be used to populate properties.
     * @returns {import("./types").RPCMessage}
     */
    #createRPCMessage(props={}, opts={}) {
        /** @type {import("./types").RPCMessage} */
        const message = {
            ...props,
            id: uuidv7(),
            ts: this.#now(),
            gen: props.gen ?? this.#generation,
        };
        if(opts?.origin) message.origin = opts.origin;
        
        // We could be able to get lane from route
        if(message.route && !message.lane) {
            message.lane = this.#laneKey(message.route, opts?.priority);
        }
        message.lane = message.lane ?? 'noLaneSet';

        // "sys" lane is meant for immediate communication like ACK or unsubscribe; no lane sequence is assumed
        if(message.lane === 'sys' && message.seq) delete message.seq;
        if(message.lane && message.lane !== 'sys') message.seq = this.#nextSeq(message.lane);

        if(!message.payload) message.payload = {};
        return message;
    }

    #startHeartbeat() {
        const period = Math.max(1000, this.settings?.protocol?.heartbeatMs ?? 5000);
        this.#stopHeartbeat();
        this.heartbeatTimer = setInterval(() => {
            if(this.webSocket && this.webSocket.readyState === WebSocket.OPEN) {
                this.#sendRaw(this.#createRPCMessage({
                    v: '0.1',
                    type: 'heartbeat',
                    lane: 'sys',
                }));
            }
        }, period);

        const awolCap = Math.max(period * 3, 10000);
        this.awolTimer = setInterval(() => {
            if(!this.webSocket) return;
            if(this.webSocket.readyState === WebSocket.CLOSED || this.webSocket.readyState === WebSocket.CLOSING) return;
            // If open but stalled, rely on server idle timeout or explicit tick
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
        
        try { this.#sendRaw(this.#createHeartbeatMessage()); } catch { /* Ignore errors */ }

        // Loss of connection detection - if more than 3 times interval, close socket.
        const awolMs = this.heartbeat.intervalMs * 3;
        if(Date.now() - this.heartbeat.lastSeen > awolMs) {
            try { this.webSocket.close(); } catch { /* ignore */ }
        }
    }

    #sendRaw(rpcMessage) {
        if(!this.webSocket || this.webSocket.readyState !== WebSocket.OPEN) return;
        try {
            this.#logOutgoingStr(rpcMessage);
            this.webSocket.send(JSON.stringify(rpcMessage));
        } catch {
            // Best-effort: if send fails, consider connection unhealthy
            try { this.webSocket.close(); } catch {/* ignored */}
        }
    }
    
    #shouldThrottle(msg) {
        if(msg.lane === 'sys') return false;
        // Messages sent with #scheduleSend are all throttled, except "sys" lane
        // Using #enqueue directly will bypass the limiter
        return true;
    }

    #scheduleSend(msg, sendFn) {
        const maxInFlight = this.settings?.protocol?.maxInFlightPerLane ?? 64;
        const lane = msg.lane || 'noLaneSet';
        const laneState = this.#getLaneState(lane);

        if(!this.#shouldThrottle(msg) || laneState.inFlight < maxInFlight) {
            if(this.#shouldThrottle(msg)) laneState.inFlight++;
            sendFn(msg); // Actually send
        } else {
            // Bound the queue to avoid memory blow-up
            const maxQueue = this.settings?.protocol?.maxQueue ?? 1024;
            if(laneState.queue.length >= maxQueue) laneState.queue.shift(); // Drop oldest
            laneState.queue.push({msg, sendFn});
        }
    }

    #onLaneDone(lane) {
        const laneState = this.#getLaneState(lane);
        laneState.inFlight = Math.max(0, laneState.inFlight - 1);
        // Drain one queued item if any
        while(laneState.queue.length > 0 && laneState.inFlight < (this.settings?.protocol?.maxInFlightPerLane ?? 64)) {
            const {msg, sendFn} = laneState.queue.shift();
            laneState.inFlight++;
            sendFn(msg); // Actually send
            break; // Drain one per completion
        }
    }

    #getLaneState(lane) {
        let laneState = this.laneStates.get(lane);
        if(!laneState) {
            laneState = {nextSeq: 1, peerAck: 0, inFlight: 0, queue: []};
            this.laneStates.set(lane, laneState);
        }
        return laneState;
    }

    #laneKey(route, _prio) {
        return route?.capability ? `cap:${route.capability}` : (route?.object ? `obj:${route.object}` : 'noValidRouteLane');
    }

    #nextSeq(lane) {
        const laneState = this.#getLaneState(lane);
        return (laneState.nextSeq++);
    }

    #pickBudgetMs(opts) {
        return opts?.budgetMs ?? this.#resolveClassCfg(opts).serviceTtlMs;
    }

    #resolveClassCfg(opts) {
        const cls = opts?.class || 'request.medium';
        const cfg = (this.settings?.timeouts?.classes?.[cls]) || {serviceTtlMs: 3000, clientPatienceExtraMs: 200};
        return cfg;
    }

    #newDeferred() {
        let resolve;
        const promise = new Promise((_resolve) => (resolve = _resolve));
        return {promise, resolve};
    }

    /**
     * Get last seen welcome immediately, if available, otherwise await the next one;
     * pass {fresh: true} to force waiting
     */
    waitForWelcome({fresh = false, signal} = {}) {
        if(!fresh && this.#lastWelcome !== null) {
            return Promise.resolve(this.#lastWelcome);
        }

        // Wait on the in-flight deferred
        const prom = this.#welcomeDeferred.promise;
        if(!signal) return prom;

        if(signal.aborted) {
            // Reject immediately with a proper AbortError
            return Promise.reject(new DOMException('Aborted', 'AbortError'));
        }

        // Create a one-off abort promise and race it with `prom`
        let onAbort;
        const abortPromise = new Promise((_resolve, reject) => {
            onAbort = () => reject(new DOMException('Aborted', 'AbortError'));
            signal.addEventListener('abort', onAbort, {once: true});
        });

        return Promise.race([prom, abortPromise]).finally(() => {
            // Cleanup if `prom` wins the race (no leak), or it's already removed if aborted
            signal.removeEventListener('abort', onAbort);
        });
    }

    /**
     * @param {string} id 
     * @param {import("./types").Invocation} invocation
     * @param {object} opts
     * @returns {import("./types").Subscription}
     */
    #makeSub(id, invocation, opts) {
        const self = this;
        const listeners = {};
        const sub = {
            on(event, fn) { listeners[event] = fn; },
            _emit(event, data) { listeners[event]?.(data); },
            close: () => { self.unsubscribe(sub.id); },
        };

        // Store a mutable id so resubscribe can update it
        Object.defineProperty(sub, 'id', {
            value: id,
            writable: true,     // Allow update on reconnect
            configurable: true, // Allow redefine if needed
            enumerable: true,
        });
        Object.defineProperty(sub, 'invocation', {
            value: invocation,
            writable: false,
            configurable: false,
            enumerable: true,
        });
        Object.defineProperty(sub, 'opts', {
            value: opts,
            writable: false,
            configurable: false,
            enumerable: true,
        });
        return sub;
    }

    async #open() {
        await this.#connectOnce();
        //this.#startHeartbeat();
        this.#flushQueue();
    }

    /**
     * Register a frontend capability the backend can call.
     * @param {string} capability The name of the capability.
     * @param {CapabilityHandlers} handlers An object with methods to handle calls, emits and subscriptions.
     */
    expose(capability, handlers) {
        if(this.localCaps.has(capability)) {
            throw new Error(`Capability already exposed: '${capability}'.`);
        }
        this.localCaps.set(capability, handlers);
        return () => this.localCaps.delete(capability);
    }
}

/* eslint-disable indent */
export function defaultSettings() {
    console.warn('Loading default settings - this shouldn\'t happen if everything is set up correctly!');
    return {
        __source: 'FRONTEND_DEFAULTS',
        protocol: {ackWaitMs: 250, graceWindowMs: 150, maxInFlightPerLane: 64, heartbeatMs: 5000, maxQueue: 1024, maxOfflineQueue: 2000},
        timeouts: {classes: {
            'request.fast': {serviceTtlMs: 800, clientPatienceExtraMs: 150},
            'request.medium': {serviceTtlMs: 3000, clientPatienceExtraMs: 200},
            'request.heavy': {serviceTtlMs: 30000, clientPatienceExtraMs: 250}},
        },
        streams: {default: {targetHz: 10, maxQueueMs: 200, coalesce: 'drop-oldest'}},
        http: {retry: 2,backoff: {baseMs: 250, maxMs: 1000, jitterPct: 30}, timeoutCapMs: 30000},
        mods: {allowSymlinks: false},
        httpProxy: {
            allowList: ['httpbin.org', 'api.openai.com', 'localhost', '127.0.0.1', '::1'],
            buckets: {default: {rpm: 600, burst: 200}},
        },
        debug: {
            backend:  {rpc: {maxPreviewChars: 1_000_000,
                             incomingMessages: {log: false, ignoreTypes: ['ack', 'heartbeat']},
                             outgoingMessages: {log: false, ignoreTypes: ['ack', 'heartbeat'], rules: [{type: 'stateUpdate', shouldLog: true, tests: [{property: 'payload.done', op: 'notExists', value: true, shouldLog: false}]}]}}},
            frontend: {rpc: {maxPreviewChars: 1_000_000,
                             incomingMessages: {log: false, ignoreTypes: ['ack', 'heartbeat'], rules: [{type: 'stateUpdate', shouldLog: true, tests: [{property: 'payload.done', op: 'notExists', value: true, shouldLog: false}]}]},
                             outgoingMessages: {log: false, ignoreTypes: ['ack', 'heartbeat']}}},
        },
    };
}
/* eslint-enable indent */
