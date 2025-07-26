const { getModLogger } = await turnixImport("../logger.js");
const { rpc } = await turnixImport("../rpc.js");

export function makeCtx(manifest) {
    return {
        modId: manifest.modId,
        viewId: window.__viewId,
        clientId: window.__clientId,
        logger: getModLogger(manifest.modId),
        registerHook: (stage, handler, opts = {}) => {
            // TODO: Implement this after separating sessions from modloader.
        },
        sendUserMessage: async (text) => {
            console.debug("Sending user message from context to frontend");
            rpc.send({
                "type": "frontendEmit",
                "action": "sendUserMessage",
                "data": {
                    "text": text,
                    "sessionId": "main",
                },
            });
        }
    };
}
