// NOTE: If you change this file, delete cache of your Electron, if you use it as client, for changes to be effective!
// Electron might cache this file, so without deleting cache Electron might still use old version of this file...

console.log("Are we running in dev mode?", isDevMode());

export function isDevMode() {    
    return globalThis.__turnixDevMode === true;
}

export function bustCache(path) {
    globalThis.__turnixSessionId ??= Date.now();
    const url = new URL(path, window.location.origin);
    url.searchParams.set("ts", globalThis.__turnixSessionId);
    return url.toString();
}

export async function turnixImport(path) {
    const url = isDevMode() ? bustCache(path) : path;
    if(isDevMode()) {
        console.debug(`[turnixImport] Loading module from path: ${url}`);
    }

    try {
        return await import(url); // Full stack trace on error
    } catch (err) {
        // TODO: Try to see if we can use logger which reports to backend
        console.error(`[Turnix] Failed to load module: ${url}`, err);
        throw err;
    }
}
