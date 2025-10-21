// frontend/bootstrap.js

// ----- Fail-fast guards -----
if(typeof globalThis.turnixImport !== 'function') {
    throw new Error('turnixImport is not defined. Ensure index.html is loaded first!');
}
if (!globalThis.crypto?.subtle?.digest) {
    console.warn('[bootstrap] Web Crypto API unavailable; hashing will throw inside sha256Hash.');
}

import { uuidv7 } from 'uuidv7'; // It's fine to use import here

const { RpcClient } = await turnixImport('/assets/rpc-client.js');
const { registerDevLogs } = await turnixImport('/assets/dev-logs.js');
const { loadMods } = await turnixImport('/assets/mod-loader.js');

function safeGet(key) {
    try { return localStorage.getItem(key); }
    catch { console.log('localSession.getItem thrown! Shouldn\'t happen. Use Electron instead...'); return null; }
}

function safeSet(key, value) {
    try { localStorage.setItem(key, value); }
    catch { console.log('localSession.setItem thrown! Shouldn\'t happen. Use Electron instead...'); }
}

function safeSessionGet(key) {
    try { return sessionStorage.getItem(key); }
    catch { console.log('sessionStorage.getItem thrown! Shouldn\'t happen. Use Electron instead...'); return null; }
}

function safeSessionSet(key, value) {
    try { sessionStorage.setItem(key, value); }
    catch { console.log('sessionStorage.setItem thrown! Shouldn\'t happen. Use Electron instead...'); }
}


const wsUrl = (location.origin.replace(/^http/, 'ws')) + '/ws';
const settings = await fetch('/settings').then(response=>response.json());

// ----- Bootstrap to get view identity via cookie+JSON -----
async function getBootstrap() {
    const res = await fetch('/api/bootstrap', {
        method: 'POST',
        credentials: 'include',
        headers: {'content-type': 'application/json'},
        body: '{}',
    });
    console.log('[bootstrap] /api/bootstrap status:', res.status);

    if(!res.ok) {
        console.error(`[bootstrap] /api/bootstrap failed: ${res.status}`);
        throw new Error(`/api/bootstrap failed with ${res.status}`);
    }

    const jsonText = await res.json();
    console.debug('[bootstrap] Response payload:', jsonText);
    return jsonText;
}


// Per-tab runtime id. Survives reconnects. Resets on hard reload/new tab.
function ensureClientInstanceId() {
    let id = safeSessionGet('clientInstanceId');
    if(!id) id = uuidv7();
    safeSessionSet('clientInstanceId', id);
    return id;
}

const boot = await getBootstrap();
const clientInstanceId = ensureClientInstanceId();
const lastKnownGenKey = `gen:${boot.viewId}`;
const lastKnownGen = Number(safeGet(lastKnownGenKey) || 0);

const rpc = await RpcClient.connect(wsUrl, {
    settings,
    hello: {
        payload: {
            viewId: boot.viewId,
            viewToken: boot.viewToken,
            clientInstanceId,
            lastKnownGen,
        }
    }
});

registerDevLogs(rpc, {ui: true});

globalThis.Turnix = {settings};
Object.freeze(globalThis.Turnix);

/**
 * @param {import("./assets/types").ModManifest} manifest
*/
function makeRpcForMod(manifest) {
    /**
     * Wraps an rpc method name and returns a callable proxy with opts containing mod origin information.
     * Opts are expected to be last argument, so if last argument is an object, origin is added to it.
     * If last argument is not object, empty object is added to arguments at last position and origin is added to the object.
     * 
     * @param {string|symbol} method - The rpc method key
     * @returns {(...args: any[]) => any} A function that forwards its args to rpc[method]
    */
    const withOrigin = (method) => (...args) => {
        const fn = rpc?.[method];
        if(typeof fn !== 'function') throw new TypeError(`makeRpcForMod(): Attempting to add mod origin to rpc.${String(method)} which is not an existing function.`);
        
        // Origin (mod info) is expected to be given in opts object
        // Last argument is expected to be opts = {} so in case it's missing, create an empty one
        // Add origin to opts
        const last = args[args.length - 1];
        const isPlainObject = (val) => Object.prototype.toString.call(val) === '[object Object]';
        const hasOpts = isPlainObject(last);
        const opts = hasOpts ? {...last} : {};
        opts.origin = {modId: manifest.id, modVersion: manifest.version};
        
        if(hasOpts) args[args.length - 1] = opts;
        else args.push(opts);
        
        return fn.apply(rpc, args);
    };
    
    return {
        request: withOrigin('request'),
        emit: withOrigin('emit'),
        subscribe: withOrigin('subscribe'),
        unsubscribe: withOrigin('unsubscribe'),
        cancel: withOrigin('cancel'),
        expose: (capability, handlers) => rpc.expose(capability, handlers),
        onWelcome: rpc.onWelcome,
        onReadyChange: rpc.onReadyChange,
        onClientReady: rpc.onClientReady,
    };
}

let sentClientReady = false;
let lastClientReadyPayload = null;
const sentForGen = new Set();

// ----- Load mods (wait for welcome, race-safe) -----
(async () => {
    const state = await rpc.waitForWelcome();
    
    // Persist current gen/version for rehydration on next reload/reconnection
    if(state?.version != null) {
        safeSet(lastKnownGenKey, String(state.version));
    }

    /** @type {import("./assets/types").ModManifest[]} */
    const manifests = state?.mods?.frontend?.modManifests ?? [];

    let loaded = [], failed = [], modsHash = '';
    try {
        ({loaded, failed, modsHash} = await loadMods(manifests, {
            makeRpcForMod: makeRpcForMod,
            settings: state?.settings ?? {__source: 'none'}
        }));
    } catch(err) {
        console.error('loadMods failed:', err);
        failed = [{ id: '(bootstrap)', reason: String(err) }];
    }

    try {
        lastClientReadyPayload = {loaded, failed, modsHash};
        if(!sentClientReady) {
            sentClientReady = true;
            await rpc.clientReady({loaded, failed, modsHash});
            // Mark current gen as sent if state.version is known
            if(typeof state?.version === 'number') sentForGen.add(state.version);
        }
    } catch(err) {
        console.error('clientReady call caused an error!', err);
    }
    
    console.log(`Loaded ${loaded.length} mod(s), failed to load ${failed.length}.`);
})();

rpc.onReadyChange = (ready) => {
    console.debug('[rpc] ready:', ready);
};

// Update lastKnownGen on any welcome (reconnect)
rpc.onWelcome = async (state) => {
    const curr = Number(safeGet(lastKnownGenKey) || 0);
    const gen = state?.version;

    if(typeof gen === 'number' && gen > curr) {
        safeSet(lastKnownGenKey, String(gen));
    }

    // Send clientReady once per generation, if we have something to report
    if(typeof gen === 'number' && lastClientReadyPayload && !sentForGen.has(gen)) {
        try {
            await rpc.clientReady(lastClientReadyPayload);
            sentForGen.add(gen);
        } catch(err) {
            console.error('clientReady on welcome failed:', err);
        }
    }

    console.debug('[rpc] welcome:', state);
};

rpc.onClientReady = (state) => {
    console.debug('[rpc] clientReady:', state);
};
