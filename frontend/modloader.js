const { rpc } = await turnixImport("./rpc.js")
const { makeCtx } = await turnixImport("./core/ctx.js");

// TODO: Implement cleaning of mods from globalThis.__turnixMods and following registries....
const eventsModsHandlers = new Map();
const ctxByMod = new Map();
const registry = {
    sessions: new Map(),
}

class Session {
    constructor({sessionId, viewId, clientId}) {
        this._sessionId = sessionId;
        this._viewId = viewId;
        this._clientId = clientId;

        this._hooks = new Map();
    }

    get sessionId() { return this._sessionId; }
    get viewId() { return this._viewId; }
    get clientId() { return this._clientId; }

    registerHook({modId, stageName, handler}) {
        console.debug(`[Session:registerHook] Mod '${modId}' wants to register a hook for '${stageName}' stage in session '${this._sessionId}'.`);

        if(!this._hooks.has(stageName)) {
            this._hooks.set(stageName, new Map());
        }
        if(this._hooks.get(stageName).has(modId)) {
            console.warn(`[Session:registerHook] Mod '${modId}' already has a hook for '${stageName}' stage registered with session ${this._sessionId}. Overriding...`);
        }
        this._hooks.get(stageName).set(modId, handler);

        // TODO: Check if registration on backend was successful.
        rpc.send({
            "type": "frontendEmit",
            "action": "registerHook",
            "data": {
                "modId": modId,
                "sessionId": this._sessionId,
                "viewId": this._viewId,
                "clientId": this._clientId,
                "stageName": stageName,
            },
        });
    }

    async callHook({modId, stageName, stageData}) {
        console.debug(`[Session:callHook] Received hook call for '${modId}' mod at '${stageName}' stage.`);
        if(this._hooks.has(stageName)) {
            if(this._hooks.get(stageName).has(modId)) {
                const handler = this._hooks.get(stageName).get(modId);
                return await handler(ctxByMod.get(modId), stageData);
            } else {
                // TODO: report error because mod didn't register hook for this stageName
                console.debug(`[Session:callHook] Mod "${modId}" didn't register hook for "${stageName}" stage.`);
            }
        } else {
            // TODO: report error because no mod registered hook for this stageName
            console.debug(`[Session:callHook] Mod "${modId}" didn't register hook for "${stageName}". This stage has no hooks at all.`)
        }
    }
}

export async function registerEventHandler(modId, eventName, handler) {
    console.debug(`[Turnix:registerEventHandler] Registering '${eventName}' event handler for mod '${modId}'.`);
    if(!eventsModsHandlers.has(eventName)) {
        eventsModsHandlers.set(eventName, new Map());
    }
    if(eventsModsHandlers.get(eventName).has(modId)) {
        console.warn(`[Turnix:registerEventHandler] Mod '${modId}' already has an event handler for '${eventName}' registered. Overwriting...`);
    }
    eventsModsHandlers.get(eventName).set(modId, handler);
    // TODO: Check if registration was successful on backend's side
    rpc.send({
        "type": "frontendEmit",
        "action": "registerEventHandler",
        "data": {
            "modId": modId,
            "eventName": eventName,
        },
    });
}

const pendingMods = new Map(); // Mods at loading phase
globalThis.__turnixMods ??= new Map(); // Mods after loading phase

function fallbackModIdFromUrl(url) {
    const parts = url.pathname.replace(/\/$/u, "").split("/");
    return parts[parts.length - 2] ?? null;
}

function trySetModId(source, manifest) {
    if(manifest.modId == null && typeof source?.modId === "string") {
        manifest.modId = source.modId.trim();
    }
}

export function makeModEntry(mod, manifest) {
    if(!mod || typeof mod !== "object") {
        throw new Error("[Turnix:makeModEntry] Mod is not a valid object.");
    }

    // Get modId from module scope if it's not present in manifest yet.
    trySetModId(mod, manifest);

    // Case 1: default export is object with functions like onActivated or onSessionCreated
    if(typeof mod.default === "object" && mod.default !== null) {
        const def = mod.default;

        // Check class instance for modId inside
        trySetModId(def, manifest);

        return def;
    }

    // Case 2: default export is class
    if(typeof mod.default === "function") {
        const Klass = mod.default;

        // Check for static modId member inside class
        trySetModId(Klass, manifest);

        // SAFETY NOTE: Be wary of constructor side effect.
        const instance = new Klass();

        // Check for instance modId member inside class
        trySetModId(instance, manifest);

        return instance;
    }

    // Case 3: mod has only one named class/function
    const values = Object.values(mod);
    const keys = Object.keys(mod);
    const constructables = values.filter((v) => typeof v === "function");

    if(constructables.length === 1) {
        const Klass = constructables[0];

        // SAFETY NOTE: Be wary of constructor side effect.
        const instance = new Klass();

        // Check for static modId member
        trySetModId(Klass, manifest);
        // Check for instance modId member
        trySetModId(instance, manifest);

        return instance;
    }

    // Case 4: named exports object
    const validExports = Object.fromEntries(
        Object.entries(mod).filter(([k, v]) => typeof v === "function" || typeof v === "object")
    );

    if(Object.keys(validExports).length > 0) {
        return validExports;
    }

    throw new Error(`[Turnix:makeModEntry] Could not resolve usable mod entry from module '${manifest.path}'. Exports: ${Object.keys(mod).join(", ")}`);
}

rpc.on("loadJSMod", async function (manifest) {
    const tStart = performance.now();
    
    const url = new URL(manifest.path, window.location)
    let mod = null;

    try {
        mod = await turnixImport(url.toString());
    } catch (err) {
        const errorMessage = `[Turnix:loadJSMod] Mod at '${manifest.path}' threw an error during import.`;
        console.error(errorMessage, err?.toString?.());
        throw err;
    }

    let modEntry = null;

    try {
        modEntry = makeModEntry(mod, manifest);
    } catch (err) {
        const errorMessage = `[Turnix:loadJSMod] Mod at '${manifest.path}' threw and error during creating mod entry.`;
        console.error(errorMessage, err?.toString?.());
        throw err;
    }

    if(typeof modEntry !== "object" || modEntry === null) {
        const errorMessage = `[Turnix:loadJSMod] Final mod entry for '${manifest.path}' is not a valid object.`;
        console.error(errorMessage)
        throw new Error(errorMessage);
    }

    // If we don't have modId by this time, fallback to the directory name.
    if(!manifest.modId) {
        manifest.modId = fallbackModIdFromUrl(url);
    }

    if(!manifest.modId) {
        const errorMessage = `[Turnix:loadJSMod] modId could not be determined for '${manifest.path}'`;
        console.error(errorMessage);
        throw new Error(errorMessage);
    }

    modEntry.__manifest = manifest;
    pendingMods.set(manifest.modId, modEntry);

    
    // Check event handlers and register those existing...
    for(const eventName of ["onDeactivate"]) {
        if(eventName in modEntry) {
            const handler = modEntry[eventName];
            registerEventHandler(manifest.modId, eventName, handler);
        }
    }
    
    const ctx = await makeCtx(manifest);
    ctxByMod.set(manifest.modId, ctx);

    const tEnd = performance.now();
    console.debug(`[Turnix:loadJSMod] Loaded mod '${manifest.modId}' in ${Math.round(tEnd - tStart)}ms (not yet activated)`);
    return manifest;
});

export class ModWrappedError extends Error {
    constructor(originalError, modId) {
        super(originalError.message);
        this.name = originalError.name;
        this.modId = modId;
        this.originalError = originalError;
        this.stack = originalError.stack;
    }
}

rpc.on("frontendHookCall", async function(data) {
    console.log(`[Turnix:frontendHookCall] Received hook call for mod '${data.modId}' for stage '${data.stageName}'`, data);
    const tStart = performance.now();

    const session = registry.sessions.get(data.sessionId);
    if(session) {
        session.callHook({
            modId: data.modId,
            stageName: data.stageName,
            stageData: data.stageData,
        });
    } else {
        // TODO: report error if session doesn't exist
        console.debug(`[Turnix:frontendHookCall] Cannot find session "${data.sessionId}" in registry.`);
    }

    const tEnd = performance.now();
    console.debug(`[Turnix:frontendHookCall] frontendHookCall event done in ${Math.round(tEnd - tStart)}ms`);
});

rpc.on("onSessionCreated", async function(data) {
    console.debug(`[Turnix:onSessionCreated] Received request to fire onSessionCreated '${data.sessionId}' event for mods.`, data);
    const tStart = performance.now();

    const session = new Session(data);
    registry.sessions.set(session.sessionId, session);
    
    for(const [modId, modEntry] of globalThis.__turnixMods) {
        if(typeof modEntry.onSessionCreated === "function") {
            console.debug(`[Turnix:onSessionCreated] Firing onSessionCreated handler for mod '${modId}'`);
            await modEntry.onSessionCreated(ctxByMod.get(modId), session);
        }
    }

    const tEnd = performance.now();
    console.debug(`[Turnix:onSessionCreated] onSessionCreated event done in ${Math.round(tEnd - tStart)}ms`);
});

rpc.on("onSessionDestroyed", async function(data) {
    console.debug(`[Turnix:onSessionDestroyed] Received request to fire onSessionDestroyed '${sessionId}' event for mods.`);
    const tStart = performance.now();

    for(const [modId, modEntry] of globalThis.__turnixMods) {
        if(typeof modEntry.onSessionDestroyed === "function") {
            console.debug(`[Turnix:onSessionDestroyed] Firing onSessionDestroyed handler for mod '${modId}'`);
            await modEntry.onSessionDestroyed(ctxByMod.get(modId), data.sessionId);
        }
    }

    registry.sessions.delete(data.sessionId);

    const tEnd = performance.now();
    console.debug(`[Turnix:onSessionDestroyed] onSessionDestroyed event done in ${Math.round(tEnd - tStart)}ms`);
});

rpc.on("deactivateJSMod", async function({ modId }) {
    // TODO: Implement this
});

rpc.on("activateJSMod", async function({ modId }) {
    const tStart = performance.now();

    const modEntry = pendingMods.get(modId);
    if(!modEntry) {
        const errorMessage = `[Turnix:activateJSMod] Received unknown modId '${modId}' to activate. This mod is not loaded.`;
        console.error(errorMessage);
        throw new Error(errorMessage);
    }

    const manifest = modEntry.__manifest;
    // Activate mod
    if(typeof modEntry.onActivate === "function") {
        try {
            await modEntry.onActivate(ctxByMod.get(modId));
        } catch (err) {
            if(err instanceof ModWrappedError) {
                console.error(`[Turnix:activateJSMod] Mod '${err.modId}' thrown an error during activation:\n${err?.toString?.()}`)
                throw err;
            } else {
                console.error(`[Turnix:activateJSMod] Mod '${modId}' thrown unexpected error during activation:\n${err?.toString?.()}`);
                throw new ModWrappedError(err, modId);
            }
        }
    }
    
    if(globalThis.__turnixMods.has(modId)) {
        console.warn(`[Turnix:activateJSMod] Mod '${modId}' was already activated. Overwriting previously activated version.`);
    }
    globalThis.__turnixMods.set(modId, modEntry);
    pendingMods.delete(modId);

    const tEnd = performance.now();
    console.debug(`[Turnix:activateJSMod] Activated mod '${manifest.modId}' in ${Math.round(tEnd - tStart)}ms`);

    return { ok: true };
});
