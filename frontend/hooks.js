import { registerFrontendHooks } from "./dirigent.js"
import { frontendBus } from "./pipeline.js"

const localHooks = []

export function registerHook(stage, name, handler, before=[], after=[]) {
    frontendBus.register(stage, name, handler, before, after);
    localHooks.push({ stage, name, handler, before, after });
}

export async function syncHooksToBackend() {
    await registerFrontendHooks(localHooks);
}
