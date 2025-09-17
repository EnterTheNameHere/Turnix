export async function loadMods(ctx) {
    const res = await fetch("/mods/index", { credentials: "same-origin" });
    const { modManifests } = await res.json();
    if(!modManifests || !modManifests.length) {
        console.warn('No mods found.');
        return [];
    }

    const loaded = [];
    for(const modManifest of modManifests) {
        try {
            const baseRpc = ctx.rpc;
            const withOrigin = (method) => (...args) => {
                const last = args[args.length - 1];
                const hasOpts = last && typeof last === "object" && !Array.isArray(last);
                const opts = hasOpts ? {...last} : {};
                // Attach origin info
                opts.origin = { modId: modManifest.id, modVersion: modManifest.version };
                if(hasOpts) args[args.length - 1] = opts; else args.push(opts);
                return baseRpc[method](...args);
            };
            const rpcForMod = {
                request: withOrigin("request"),
                emit: withOrigin("emit"),
                subscribe: withOrigin("subscribe"),
                unsubscribe: withOrigin("unsubscribe"),
                cancel: withOrigin("cancel"),
                expose: withOrigin("expose"),
            };

            const api = await turnixImport(modManifest.entry);
            if(typeof api?.onLoad === "function") {
                await api.onLoad({ ...ctx, rpc: rpcForMod, manifest: modManifest });
                console.log(`Loaded mod: ${modManifest.name} (${modManifest.id})`);
                loaded.push({ id: modManifest.id, api, manifest: modManifest });
            } else {
                console.warn(`Mod has no onLoad(): ${modManifest.id}`);
            }
        } catch(ex) {
            console.error("Failed to load mod", modManifest.id, ex);
        }
    }

    return loaded;
}
