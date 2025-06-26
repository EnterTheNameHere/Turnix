import { registerHook, syncHooksToBackend } from "./hooks.js"

export async function loadFrontendMods() {
    registerHook("PostInput", "mod:ui_logger", (data) => {
        console.log("User typed:", data.userMessage);
        return data;
    });

    await syncHooksToBackend();
}
