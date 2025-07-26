let defaultModId = "frontend";
let rpcSend = null;

export function configureLogger({ sendRpc, modId = "frontend" }) {
    rpcSend = sendRpc;
    defaultModId = modId;
}

function log(level = "info", args, modId=defaultModId) {
    const message = args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join("");
    const timestamp = new Date().toISOString();

    // Console
    switch (level) {
        case "debug":   console.debug(`[${level.toUpperCase()}]`, message); break;
        case "info":    console.info(`[${level.toUpperCase()}]`, message); break;
        case "warn":    console.warn(`[WARNING]`, message); break;
        case "warning": console.warn(`[${level.toUpperCase()}]`, message); break;
        case "error":   console.error(`[${level.toUpperCase()}]`, message); break;
        default:        console.log(`[${level.toUpperCase()}]`, message);
    }

    if(rpcSend) {
        rpcSend("logMessage", {
            level,
            message,
            modId,
            timestamp,
        }, false); // We don't want response
    }
}

export function getModLogger(modId) {
    return {
        debug:   (...args) => log("debug", args, modId),
        info:    (...args) => log("info", args, modId),
        warn:    (...args) => log("warning", args, modId),
        warning: (...args) => log("warning", args, modId),
        error:   (...args) => log("error", args, modId),
    };
}
