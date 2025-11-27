// frontend/core/mod-loader.js

export async function sha256Hash(input) {
    if(!globalThis.crypto?.subtle?.digest) {
        throw new Error('Web Crypto API (crypto.subtle) is unavailable. Use a modern browser/runtime.');
    }
    if(typeof input !== 'string' || input.trim().length === 0) {
        throw new TypeError('sha256Hash expects a non-empty string.');
    }
    const textUint8 = new TextEncoder().encode(input);
    const hashBuffer = await globalThis.crypto.subtle.digest('SHA-256', textUint8);
    return Array.from(new Uint8Array(hashBuffer)).map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * @param {import("../assets/types").ModManifest[]} manifests
 */
export async function loadMods(manifests, {makeRpcForMod, settings = {}} = {}) {
    if(!manifests) throw new Error('[mod-loader] loadMods() expects manifests list as an argument');
    if(!manifests.length) console.warn('[mod-loader] loadMods() received empty manifests list');
    const enabledMods = (manifests || []).filter(manifest => manifest && manifest.enabled);
    const loaded = [];
    const failed = [];
    
    if(enabledMods.length === 0) {
        return {loaded, failed, modsHash: await sha256Hash('[]')};
    }

    // NOTE: Sequential load until graph of dependencies is implemented.
    for(const manifest of enabledMods) {
        try {
            const api = await turnixImport(manifest.entry);
            const onLoad = typeof api?.onLoad === 'function' ? api.onLoad : null;
            
            let rpcForMod = null;
            if(makeRpcForMod) {
                try {
                    rpcForMod = makeRpcForMod(manifest);
                } catch(err) {
                    console.warn(`[mod-loader] Error caught when creating RPC for mod '${manifest.id}'.`, err);
                    throw err;
                }
            }
                
            if(onLoad) {
                await onLoad({rpc: rpcForMod, manifest, settings});
            } else {
                console.warn(`[mod-loader] onLoad() not found in mod: ${manifest?.id}`);
                // Mod without onLoad() is still considered as loaded.
            }

            loaded.push({id: manifest.id, version: manifest.version, hash: manifest.hash});
        } catch(err) {
            console.error('[mod-loader] Failed to load mod:', manifest?.id, err);
            failed.push({
                id: manifest?.id ?? 'unknown',
                reason: String(err?.message || err),
                stack: err?.stack
            });
        }
    }

    // Sort to keep hash stable regardless of manifest order
    const basis = enabledMods
        .map(({id, version, hash}) => ({id, version, hash}))
        .sort((a,b) => (a.id ?? '').localeCompare(b.id ?? '') || (a.version ?? '').localeCompare(b.version ?? ''));

    const modsHash = await sha256Hash(JSON.stringify(basis));
    return {loaded, failed, modsHash};
}
