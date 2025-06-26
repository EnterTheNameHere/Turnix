const backendUrl = "http://localhost:8000";

export async function registerFrontendHooks(hooks) {
    const payload = { hooks: hooks.map(hook => ({
        stage: hook.stage,
        name: hook.name,
        before: hook.before || [],
        after: hook.after || [],
    }))};
    
    await fetch(`${backendUrl}/register_frontend_hooks`, {
        method: "POST",
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
}

export async function callBackendPipeline(stage, data) {
    const response = await fetch(`${backendUrl}/run_pipeline`, {
        method: "POST",
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage, data })
    });
    const workerData = await response.json();
    return workerData;
}
