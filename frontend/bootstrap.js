// ----- Fail-fast guards -----
if(typeof globalThis.turnixImport !== "function") {
    throw new Error("turnixImport is not defined. Ensure index.html is loaded first!");
}
if (!globalThis.crypto?.subtle?.digest) {
  console.warn("[bootstrap] Web Crypto API unavailable; hashing will throw inside sha256Hash.");
}

const { RpcClient } = await turnixImport("/assets/rpc-client.js");
const { loadMods } = await turnixImport("/assets/mod-loader.js");

const wsUrl = (location.origin.replace(/^http/, 'ws')) + '/ws';
const settings = await fetch('/settings').then(response=>response.json());
const rpc = await RpcClient.connect(wsUrl, {settings});

globalThis.Turnix = {rpc, settings};

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
       if(typeof fn !== "function") throw new TypeError(`makeRpcForMod(): Attempting to add mod origin to rpc.${String(method)} which is not an existing function.`);
       
       // Origin (mod info) is expected to be given in opts object
       // Last argument is expected to be opts = {} so in case it's missing, create an empty one
       // Add origin to opts
       const last = args[args.length - 1];
       const hasOpts = last && typeof last === "object" && !Array.isArray(last);
       const opts = hasOpts ? {...last} : {};
       opts.origin = {modId: manifest.id, modVersion: manifest.version};
       
       if(hasOpts) args[args.length - 1] = opts;
       else args.push(opts);
       
       return fn.apply(rpc, args);
    };
    
    return {
        request: withOrigin("request"),
        emit: withOrigin("emit"),
        subscribe: withOrigin("subscribe"),
        unsubscribe: withOrigin("unsubscribe"),
        cancel: withOrigin("cancel"),
        expose: (capability, handlers) => rpc.expose(capability, handlers),
        onWelcome: rpc.onWelcome,
        onReadyChange: rpc.onReadyChange,
        onClientReady: rpc.onClientReady,
    };
}

// ----- Load mods (wait for welcome, race-safe) -----
(async () => {
    const state = await rpc.waitForWelcome();
    
    /** @type {import("./assets/types").ModManifest[]} */
    const manifests = state?.mods?.frontend?.modManifests ?? [];

    const {loaded, failed, modsHash} = await loadMods(manifests, {
        makeRpcForMod: makeRpcForMod,
        settings: state?.settings ?? {__source: "none"}
    });

    try {
        await rpc.clientReady({loaded, failed, modsHash});
    } catch(err) {
        console.error("clientReady call caused an error!", err);
    }
    
    console.log(`Loaded ${loaded.length} mod(s), failed to load ${failed.length}.`);
})();

rpc.onReadyChange = (ready) => {
    console.debug('[rpc] ready:', ready);
}

rpc.onWelcome = async (state) => {
    console.debug('[rpc] welcome:', state);
}

rpc.onClientReady = (state) => {
    console.debug('[RPC] clientReady:', state);
}
