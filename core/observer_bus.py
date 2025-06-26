import time
import asyncio
from typing import Callable, Any, Dict, List
from core.pipeline_stages import PipelineStage
from core.logger import getProfilerLogger
from backend.rpc_websocket import pushEvent, activeSocket, pendingRequests

import logging
logger = logging.getLogger(__name__)
profiler = getProfilerLogger()

class ObserverHook:
    def __init__(self, stage: str, name: str, handler: Callable, before=None, after=None, location: str="backend"):
        self.stage = stage
        self.name = name
        self.handler = handler
        self.before = before or []
        self.after = after or []
        self.location = location

class ObserverBus:
    def __init__(self):
        self.hooks: Dict[str, Dict[str, ObserverHook]] = {}
        self.sortedHooks: Dict[str, List[ObserverHook]] = {}
    
    def register(self, stage: str, name: str, handler: Callable, before=None, after=None, location: str="backend"):
        if stage not in self.hooks:
            self.hooks[stage] = {}
        
        # Reloading page will cause hooks in frontend
        # to re-register themselves, so we need to handle
        # that case here.
        if name in self.hooks[stage]:
            existing = self.hooks[stage][name]
            if existing.location != location:
                raise ValueError(f"Hook '{name}' already exists for stage '{stage}' with different location!")
            logger.info(f"Duplicate registration ignored for hook '{name}' as stage '{stage}'.")
            return
        
        hook = ObserverHook(stage=stage, name=name, handler=handler, before=before, after=after, location=location)
        self.hooks[stage][name] = hook
        self.sortedHooks.pop(stage, None)

    async def run(self, stage: str, data: Any) -> Any:
        hooks = self._getSortedHooks(stage)
        logger.debug(f"Running pipeline stage {stage} ({len(hooks)} hooks).")
        for hook in hooks:
            logger.debug(f"Running hook '{hook.name}' (location={hook.location}).")
            inputDataId = id(data)
            startTime = time.perf_counter()
            
            try:
                if hook.location == "backend":
                    outputData = hook.handler(data)
                else:
                    outputData = await self.callFrontend(hook, stage, data)
                duration = (time.perf_counter() - startTime) * 1000 # ms
                logger.debug(f"Hook '{hook.name}' completed in {duration:.2f} ms.")
            except Exception as e:
                logger.error(f"Hook '{hook.name}' raised an exception: {e}")
                continue # skip this hook but keep going
                
            # TODO: Disable mod if it fails multiple times
            if outputData is None:
                logger.error(f"Hook '{hook.name}' did not return data. Using previous data state.")
                outputData = data
            elif id(outputData) != inputDataId:
                logger.warning(f"Hook '{hook.name}' returned a new object. Same data object should be returned.")

            data = outputData
        return data

    def _getSortedHooks(self, stage: str) -> List[ObserverHook]:
        if stage in self.sortedHooks:
            return self.sortedHooks[stage]
        hooks = list(self.hooks.get(stage, {}).values())
        order = self._resolveOrder(hooks)
        self.sortedHooks[stage] = order
        return order
    
    def _resolveOrder(self, hooks: List[ObserverHook]) -> List[ObserverHook]:
        sortedList = []
        remaining = {hook.name: hook for hook in hooks}
        visited = set()

        def visit(hook: ObserverHook):
            if hook.name in visited:
                return
            for dependency in hook.before:
                if dependency in remaining:
                    visit(remaining[dependency])
            visited.add(hook.name)
            sortedList.append(hook)
        
        while remaining:
            name, hook = remaining.popitem()
            visit(hook)
        
        return sortedList

    async def callFrontend(self, hook: ObserverHook, stage: str, data: Any):
        if not activeSocket:
            logger.error(f"Cannot call frontend hook: '{hook.name}', no active socket connection.")
            return data
        
        future = asyncio.get_event_loop().create_future()
        requestId = f"{hook.stage}:{hook.name}:{int(time.time() * 1000)}"
        
        def resolve(result):
            if not future.done():
                future.set_result(result)
        
        def reject(err):
            if not future.done():
                future.set_result(data) # Fallback to last data
        
        pendingRequests[requestId] = { "resolve": resolve, "reject": reject }

        await pushEvent("frontendHook", {
            "name": hook.name,
            "stage": stage,
            "data": data.model_dump(by_alias=True),
            "requestId": requestId
        })

        try:
            result = await asyncio.wait_for(future, timeout=5)
            return type(data)(**result)
        except Exception as e:
            logger.error(f"Timeout of failure calling frontend hook '{hook.name}': {e}")
            return data
        finally:
            pendingRequests.pop(requestId, None)
