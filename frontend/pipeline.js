export class FrontendObserverBus {
    constructor() {
        this.hooks = {};
    }

    register(stage, name, handler, before=[], after=[]) {
        if(!this.hooks[stage]) {
            this.hooks[stage] = {}
        }
        this.hooks[stage][name] = { handler, before, after };
    }

    async execute(stage, hookName, data) {
        const hook = this.hooks[stage]?.[hookName];
        if(!hook) {
            console.warn(`"${stage}": attempting to execute hook "${hookName}" which is not registered!`);
            return data;
        }
        return await hook.handler(data);
    }
}

export const frontendBus = new FrontendObserverBus();
