import time
from typing import Callable, Any, Dict, List
from core.logger import getProfilerLogger

import logging
logger = logging.getLogger(__name__)
profiler = getProfilerLogger()

class ObserverHook:
    def __init__(self, stageName: str, modId: str, handler: Callable, before=None, after=None, location: str="backend"):
        self.stageName = stageName
        self.modId = modId
        self.handler = handler
        self.before = before or []
        self.after = after or []
        self.location = location

class ObserverBus:
    def __init__(self, view, session):
        self.hooks: Dict[str, Dict[str, ObserverHook]] = {}
        self.sortedHooks: Dict[str, List[ObserverHook]] = {}
        self.view = view
        self.session = session
    
    async def register(self, data):
        stageName = data.get("stageName")

        print(f"Registering hook for '{stageName}'", data)
        if stageName not in self.hooks:
            self.hooks[stageName] = {}
        
        location = data.get("location")

        # Reloading page will cause hooks in frontend
        # to re-register themselves, so we need to handle
        # that case here.
        modId = data.get("modId")
        if modId in self.hooks[stageName]:
            existing = self.hooks[stageName][modId]
            if existing.location != location:
                raise ValueError(f"Mod '{modId}' is trying to register '{stageName}' hook which is already registered, but this time with different location!")
            logger.info(f"Mod '{modId}' is trying to register hook for a stage {stageName} ")
            return
        
        before = data.get("before", None)
        after = data.get("after", None)
        handler = data.get("handler", None)

        hook = ObserverHook(stageName=stageName, modId=modId, handler=handler, before=before, after=after, location=location)
        self.hooks[stageName][modId] = hook
        self.sortedHooks.pop(stageName, None)

    async def run(self, stageName: str, data: Any) -> Any:
        hooks = self._getSortedHooks(stageName)
        logger.debug(f"Running pipeline stage '{stageName}' ({len(hooks)} hooks).")
        for hook in hooks:
            logger.debug(f"Running hook '{hook.modId}' (location={hook.location}).")
            inputDataId = id(data)
            startTime = time.perf_counter()
            
            try:
                if hook.location == "backend":
                    outputData = hook.handler(data)
                else:
                    outputData = await self.callFrontend(hook, stageName, data)
                duration = (time.perf_counter() - startTime) * 1000 # ms
                logger.debug(f"Hook '{hook.modId}' completed in {duration:.2f} ms.")
            except Exception:
                logger.exception(f"Hook '{hook.modId}' raised an exception.")
                continue # skip this hook but keep going
                
            # TODO: Disable mod if it fails multiple times
            if outputData is None:
                logger.error(f"Hook '{hook.modId}' did not return data. Using previous data state.")
                outputData = data
            elif id(outputData) != inputDataId:
                logger.warning(f"Hook '{hook.modId}' returned a new object. Same data object should be returned.")

            # TODO: Some data should not change, like the sessionId so we should check that...

            data = outputData
        return data

    def _getSortedHooks(self, stageName: str) -> List[ObserverHook]:
        if stageName in self.sortedHooks:
            return self.sortedHooks[stageName]
        hooks = list(self.hooks.get(stageName, {}).values())
        order = self._resolveOrder(hooks)
        self.sortedHooks[stageName] = order
        return order
    
    def _resolveOrder(self, hooks: List[ObserverHook]) -> List[ObserverHook]:
        sortedList = []
        remaining = {hook.modId: hook for hook in hooks}
        visited = set()

        def visit(hook: ObserverHook):
            if hook.modId in visited:
                return
            for dependency in hook.before:
                if dependency in remaining:
                    visit(remaining[dependency])
            visited.add(hook.modId)
            sortedList.append(hook)
        
        while remaining:
            _, hook = remaining.popitem()
            visit(hook)

        return sortedList

    async def callFrontend(self, hook: ObserverHook, stageName: str, data: Any):
        # TODO: Make sure frontend call is received
        await self.view.send({
            "type": "backendEmit",
            "action": "frontendHookCall",
            "stageName": stageName,
            "modId": hook.modId,
            "sessionId": self.session.sessionId,
            "viewId": self.view.viewId,
            "clientId": self.view.clientId,
            "securityToken": self.view.securityToken,
            "timestamp": int(time.time() * 1000),
            "data": {
                "stageData": data.model_dump(),
                "modId": hook.modId,
                "sessionId": self.session.sessionId,
                "stageName": stageName,
            },
        })
