// NOTE: If you change this file, delete cache of your Electron, if you use it as client, for changes to be effective!
// Electron might cache this file, so without deleting cache Electron might still use old version of this file...

console.log('Are we running in dev mode?', isDevMode());

/**
 * @returns {boolean}
 */
export function isDevMode() {    
    return globalThis.__turnixDevMode === true;
}

/**
 * @returns {string}
 */
function getBaseForURL() {
    // href is always a full URL even under file:// in Electron
    return window.location.href ?? 'http://localhost/';
}

/**
 * @param {string|URL} path
 * @returns {string}
 */
function asURLString(path) {
    return (path instanceof URL ? path.toString() : String(path));
}

/**
 * @param {string|URL} path
 * @returns {string}
 */
export function bustCache(path) {
    // Keep ts stable per session
    globalThis.__turnixCacheBustToken ??= (globalThis.crypto?.randomUUID?.() ?? Date.now().toString());
    try {
        const url = new URL(asURLString(path), getBaseForURL());
        // Overwrite any existing ts param by stable one
        url.searchParams.set('ts', globalThis.__turnixCacheBustToken);
        return url.toString();
    } catch {
        // If path is something weird (missing protocol and base failed),
        // fall back to treating it as-is; dynamic import might still deal with it...
        return asURLString(path);
    }
}

/**
 * @type {Map<string, Promise<any>>}
 */
const __moduleMemo = new Map();

/**
 * Dynamically import a module, with dev cache busting and session memo.
 * @param {string|URL} path - Module path or absolute URL.
 * @param {{noBust?: boolean}} [opts] - In case busting is undesired.
 */
export async function turnixImport(path, opts = {}) {
    const {noBust = false} = opts;
    const finalURL = (isDevMode() && !noBust) ? bustCache(path) : asURLString(path);
    
    if(isDevMode()) {
        console.debug(`[turnixImport] Loading module from path: ${finalURL}`);
    }

    // Reuse the same promise in the same session to avoid refresh
    if(__moduleMemo.has(finalURL)) {
        return __moduleMemo.get(finalURL);
    }

    try {
        const pp = import(finalURL); // Full stack trace on error
        __moduleMemo.set(finalURL, pp);
        return await pp;
    } catch (err) {
        // TODO: Try to see if we can use logger which reports to backend
        if(isDevMode()) console.error(`[Turnix] Failed to load module: ${finalURL}\n${err?.stack || err}`);
        else console.error(`[Turnix] Failed to load: ${finalURL}`);
        // Don't keep failures memoized
        __moduleMemo.delete(finalURL);
        throw err;
    }
}
