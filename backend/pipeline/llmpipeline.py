# backend/pipeline/llmpipeline.py
from __future__ import annotations
import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, Literal

from backend.app.globals import getTracer
from backend.core.ids import uuid_12
from backend.memory.memory_layer import MemoryPropagator
from backend.sessions.session import Session, SessionKind, SessionVisibility

__all__ = ["LLMPipeline", "LLMPipelineRun", "StageHandler", "EngineCaller"]



StageHandler = Callable[["LLMPipelineRun", dict | None], Awaitable[dict | None] | None]
EngineCaller = Callable[["LLMPipelineRun"], Awaitable[object] | object]



class _EventBus:
    def __init__(self) -> None:
        # topic -> set(queue)
        self._subscriptions: dict[str, set[asyncio.Queue[Any]]] = {}
    
    async def publish(self, topic: str, event: dict) -> None:
        queues = list(self._subscriptions.get(topic, set()))
        for queue in queues:
            # Best-effort. Never block indefinitely.
            try:
                queue.put_nowait(event)
            except Exception:
                # Slow/broken consumer - drop
                pass
    
    def subscribe(self, topic: str):
        """
        Returns an async iterator: 'async for ev in bus.subscribe(topic): ...'
        Cancelling the consumer task unsubscribes automatically.
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._subscriptions.setdefault(topic, set()).add(queue)
        
        async def _gen():
            try:
                while True:
                    ev = await queue.get()
                    yield ev
            finally:
                try:
                    self._subscriptions.get(topic, set()).discard(queue)
                except Exception:
                    pass
        return _gen()



class LLMPipelineStages(str, Enum):
    PrepareInput = "PrepareInput"
    BuildQueryItems = "BuildQueryItems"
    FilterQueryItems = "FilterQueryItems"
    BuildPrompt = "BuildPrompt"
    EngineCall = "EngineCall"
    ParseStreamedResponse = "ParseStreamedResponse"
    ParseResponse = "ParseResponse"
    UpdateQueryItems = "UpdateQueryItems"
    Finalize = "Finalize"



DEFAULT_STAGE_ORDER: list[LLMPipelineStages] = [
    LLMPipelineStages.PrepareInput,
    LLMPipelineStages.BuildQueryItems,
    LLMPipelineStages.FilterQueryItems,
    LLMPipelineStages.BuildPrompt,
    LLMPipelineStages.EngineCall,
    LLMPipelineStages.ParseStreamedResponse,
    LLMPipelineStages.ParseResponse,
    LLMPipelineStages.UpdateQueryItems,
    LLMPipelineStages.Finalize,
]



def _normalizeStageId(stageId: str | LLMPipelineStages) -> LLMPipelineStages:
    if isinstance(stageId, LLMPipelineStages):
        return stageId
    # Accept either enum.name or enum.value; prefer value match first
    for stage in LLMPipelineStages:
        if stageId == stage.value or stageId == stage.name:
            return stage
    raise ValueError(f"unknown stage id: {stageId!r}")



class LLMPipeline:
    """
    Orchestrates stage progression and mod subscriptions.
    Each Session has exactly one LLMPipeline instance.
    """
    def __init__(self, *, ownerSession: Session):
        self.ownerSession: Session = ownerSession
        self.version: int = 0
        self.stageOrder: list[LLMPipelineStages] = list(DEFAULT_STAGE_ORDER)
        # stage -> subId -> (priority: int, mode: str, handler: StageHandler)
        self.stageSubscriptions: dict[LLMPipelineStages, dict[str, tuple[int, str, StageHandler]]] = {}
        self._activeRuns: dict[str, asyncio.Task[Any]] = {}
        self._engineCaller: EngineCaller | None = None
        self._engineCallBeforeFanout: bool = True
        self.events: _EventBus = _EventBus()
        
        # Subscribers for spawned sessions
        self.hiddenSessionSubscribers: dict[str, StageHandler] = {}
        self.temporarySessionSubscribers: dict[str, StageHandler] = {}

    # ----- Subscriptions -----

    def subscribeToStage(
        self,
        stageId: str | LLMPipelineStages,
        handler: StageHandler,
        *,
        priority: int = 0,
        mode: str = "once" # "once" | "perChunk",
    ) -> str:
        stage = _normalizeStageId(stageId)
        subId = uuid_12("sub_")
        self.stageSubscriptions.setdefault(stage, {})[subId] = (int(priority), str(mode), handler)
        return subId

    def unsubscribe(self, subscriptionId: str) -> bool:
        for _stage, subs in self.stageSubscriptions.items():
            if subscriptionId in subs:
                del subs[subscriptionId]
                return True
        return False
    
    # ----- Spawn session notification (opt-in) -----
    
    def onHiddenSessionCreated(self, handler: StageHandler) -> str:
        subId = uuid_12("subHiddenSessionNotification_")
        self.hiddenSessionSubscribers[subId] = handler
        return subId
    
    def offHiddenSessionCreated(self, subscriptionId: str) -> bool:
        """
        Unsubscribe a previously registered hidden-session notification handler.
        """
        if subscriptionId in self.hiddenSessionSubscribers:
            del self.hiddenSessionSubscribers[subscriptionId]
            return True
        return False
    
    def onTemporarySessionCreated(self, handler: StageHandler) -> str:
        subId = uuid_12("subTemporarySessionNotification_")
        self.temporarySessionSubscribers[subId] = handler
        return subId
    
    def offTemporarySessionCreated(self, subscriptionId: str) -> bool:
        """
        Unsubscribe a previously registered temporary-session notification handler.
        """
        if subscriptionId in self.temporarySessionSubscribers:
            del self.temporarySessionSubscribers[subscriptionId]
            return True
        return False
    
    # ----- Engine caller injection -----
    
    def setEngineCaller(self, caller: EngineCaller) -> None:
        """
        Register the engine adapter. It must return an **async-iterable** of chunk dicts
        (or an awaitable resolving to that). LLMPipeline will `async for` it, dispatching
        chunks to `ParseStreamedResponse` subscribers (mode="perChunk") and only then
        continue to the next stages.
        """
        self._engineCaller = caller
    
    def setEngineCallOrder(self, *, beforeFanout: bool = True) -> None:
        """
        Controls whether EngineCall runs before or after stage fanout.
        - beforeFanout=True (default): engine is invoked first, then observers run.
        - beforeFanout=False: observers run first (to prepare inputs), then engine. 
        """
        self._engineCallBeforeFanout = bool(beforeFanout)
    
    # ----- Runs -----

    def startRun(self, *, kind: str, initialInput: dict[str, Any]) -> LLMPipelineRun:
        run = LLMPipelineRun(
            pipeline=self,
            ownerSession=self.ownerSession,
            kind=kind,
            initialInput=initialInput,
        )
        getTracer().updateTraceContext({
            "sessionId": getattr(self.ownerSession, "sessionId", None),
            "pipelineRunId": run.runId,
        })
        task = asyncio.create_task(self._runTask(run), name=f"llmpipeline:{run.runId}")
        self._activeRuns[run.runId] = task
        return run

    async def _runTask(self, run: LLMPipelineRun) -> None:
        tracer = getTracer()
        span = tracer.startSpan(
            "pipeline.run",
            attrs={"kind": run.kind},
            tags=["pipeline"],
            contextOverrides={
                "sessionId": getattr(run.ownerSession, "sessionId", None),
                "pipelineRunId": run.runId,
            },
        )
        tracer.traceEvent(
            "pipeline.start",
            attrs={"kind": run.kind},
            level="info",
            tags=["pipeline"],
            span=span,
        )
        
        try:
            for stage in self.stageOrder:
                run.stage = stage
                
                tracer.traceEvent(
                    "pipeline.stage.enter",
                    attrs={"stage": stage.value},
                    tags=["pipeline", "stage"],
                    span=span,
                )
                
                if stage == LLMPipelineStages.EngineCall and self._engineCaller is not None:
                    # Optionally let observers tweak engineRequest before starting the stream
                    if not self._engineCallBeforeFanout:
                        await self._fanout(stage, run, None)
                        if run.status != "running":
                            break
                    
                    # Acquire an async-iterable stream from the engine adapter
                    streamObj = self._engineCaller(run)
                    if asyncio.iscoroutine(streamObj):
                        streamObj = await streamObj
                    # Validate async-iterable
                    if not hasattr(streamObj, "__aiter__"):
                        raise TypeError("EngineCaller must return an async-iterable stream")
                    
                    # Stream loop → emit per-chunk to ParseStreamedResponse (mode='perChunk')
                    try:
                        async for chunk in streamObj:
                            # Allow cancellation/failure at any time
                            if run.status != "running":
                                break
                            try:
                                await self._emitChunk(LLMPipelineStages.ParseStreamedResponse, run, chunk or {})
                            except Exception as err:
                                # Do not fail the run because a per-chunk observer exploded...
                                errs = run.runCtx.setdefault("chunkErrors", [])
                                if isinstance(errs, list):
                                    errs.append(f"ParseStreamedResponse: {err}")
                    except asyncio.CancelledError:
                        raise
                    except Exception as err:
                        run.fail(f"engineStreamError: {err}")
                        break
                    
                    # If EngineCall has pre-fanout mode, run observers now (e.g., metrics)
                    if self._engineCallBeforeFanout:
                        await self._fanout(stage, run, None)
                        if run.status != "running":
                            break
                else:
                    # Normal stages: full fanout (ParseStreamedResponse is chunk-only. Skip here)
                    if stage != LLMPipelineStages.ParseStreamedResponse:
                        await self._fanout(stage, run, None)
                
                tracer.traceEvent(
                    "pipeline.stage.exit",
                    attrs={"stage": stage.value, "status": run.status},
                    tags=["pipeline", "stage"],
                    span=span,
                )
                
                if run.status != "running":
                    # Handler might cancel/fail the run
                    break
            
            # Terminalization + memory boundary (early-fail)
            if run.status == "running":
                # Success path: commit staged changes from txn to real layers
                try:
                    run.ownerSession.saveMemory()
                except Exception as ex:
                    # Rollback and fail the run
                    try:
                        propagator = MemoryPropagator(run.ownerSession.memoryResolver)
                        propagator.rollback(run.ownerSession.memoryLayers)
                    finally:
                        run.fail(f"commitFailed: {ex}")
                if run.status == "running":
                    run._finish("succeeded")
            else:
                # Failure/cancel path: rollback txn explicitly
                try:
                    propagator = MemoryPropagator(run.ownerSession.memoryResolver)
                    propagator.rollback(run.ownerSession.memoryLayers)
                except Exception:
                    # Best-effort rollback. We still close the run below...
                    pass
                
        except asyncio.CancelledError:
            # External cancel → rollback and mark cancelled
            try:
                propagator = MemoryPropagator(run.ownerSession.memoryResolver)
                propagator.rollback(run.ownerSession.memoryLayers)
            finally:
                run._finish("cancelled")
            raise
        except Exception as ex:
            # Any exception → rollback and mark failed
            try:
                propagator = MemoryPropagator(run.ownerSession.memoryResolver)
                propagator.rollback(run.ownerSession.memoryLayers)
            finally:
                run.fail(f"pipelineError: {ex}")
            raise
        finally:
            self._activeRuns.pop(run.runId, None)
            
            try:
                status = run.status
                statusMap = {
                    "succeeded": "ok",
                    "failed": "error",
                    "cancelled": "cancelled",
                    "running": "error",
                }
                traceStatus = statusMap.get(status, "error")
                attrs: dict[str, Any] = {"runStatus": status}
                
                err = run.runCtx.get("error")
                if isinstance(err, str) and err:
                    attrs["error"] = err
                
                cancelReason = run.runCtx.get("cancelReason")
                if isinstance(cancelReason, str) and cancelReason:
                    attrs["cancelReason"] = cancelReason
                
                tracer.traceEvent(
                    "pipeline.end",
                    attrs={"status": status},
                    level="info",
                    tags=["pipeline"],
                    span=span,
                )
                
                tracer.endSpan(
                    span,
                    status=traceStatus,
                    level="info",
                    tags=["pipeline"],
                    attrs=attrs,
                )
            except Exception:
                # Tracing must never break pipeline teardown.
                pass
            

    async def _fanout(self, stage: LLMPipelineStages, run: LLMPipelineRun, payload: dict | None = None) -> dict | None:
        """
        Fanout in priority order. Handlers may return a dict to merge into runCtx.
        The last non-None return value is also returned to the caller (Finalize uses this).
        """
        subscriptions = list(self.stageSubscriptions.get(stage, {}).values())
        # Sort by priority asc (-100 runs before 0, and 0 before +100)
        subscriptions.sort(key=lambda item: item[0])
        lastReturned: dict | None = None
        for _priority, mode, handler in subscriptions:
            # Only deliver "once" handlers here. "perChunk" are invoked by emitChunk()
            if mode != "once":
                continue
            try:
                maybe = handler(run, payload)
                if inspect.isawaitable(maybe):
                    maybe = await maybe
                if isinstance(maybe, dict):
                    # Merge into runCtx (shallow is enough for stage coordination)
                    run.runCtx.update(maybe)
                    lastReturned = maybe
            except Exception as err:
                run.fail(f"handlerError@{stage.value}: {err}")
                break
            if run.status != "running":
                break
        return lastReturned
    
    async def _emitChunk(self, stage: LLMPipelineStages, run: LLMPipelineRun, chunk: dict) -> None:
        """
        Delivers per-chunk notifications to handlers registered with mode='perChunk'.
        These must not fail the run. Exceptions are caught and appended to runCtx['chunkErrors'].
        """
        subscriptions = list(self.stageSubscriptions.get(stage, {}).values())
        # Sort by priority asc (-100 runs before 0, and 0 before +100)
        subscriptions.sort(key=lambda item: item[0])
        for _priority, mode, handler in subscriptions:
            if mode != "perChunk":
                continue
            try:
                maybe = handler(run, chunk)
                if inspect.isawaitable(maybe):
                    await maybe
            except Exception as err:
                errs = run.runCtx.setdefault("chunkErrors", [])
                if isinstance(errs, list):
                    errs.append(f"{stage.value}: {err}")
    
    def activeRunIds(self) -> list[str]:
        return list(self._activeRuns.keys())
    
    async def awaitRun(self, runId: str) -> None:
        task = self._activeRuns.get(runId)
        if task:
            await asyncio.shield(task)

    def cancelRun(self, runId: str) -> bool:
        task = self._activeRuns.get(runId)
        if not task:
            return False
        task.cancel()
        return True
    
    # Convenience alias
    def cancel(self, runId: str) -> bool:
        return self.cancelRun(runId)
    
    def cancelAllRuns(self) -> None:
        for task in list(self._activeRuns.values()):
            task.cancel()
        self._activeRuns.clear()



class LLMPipelineRun:
    """
    Ephemeral per-call run context. Shared mutable bag (runCtx) plus lifecycle.
    Also owns helpers to spawn hidden/temporary sessions on-demand.
    """
    def __init__(
        self,
        *,
        pipeline: LLMPipeline,
        ownerSession: Session,
        kind: str,
        initialInput: dict[str, Any]
    ):
        self.pipeline: LLMPipeline = pipeline
        self.ownerSession: Session = ownerSession
        self.runId: str = uuid_12("pipelineRun_")
        self.kind: str = kind
        self.createdTs: float = time.time()
        self.finishedTs: float | None = None
        self.status: Literal["running", "succeeded", "failed", "cancelled"] = "running"
        self.stage: LLMPipelineStages | None = None

        # Shared, free-form context for handlers
        self.runCtx: dict[str, Any] = {
            "input": dict(initialInput or {}),
            "queryItems": [],
            "promptDraft": {},
            "engineRequest": None,
            "engineResponse": None,
            "extractedArtifacts": {},
            "messagesDelta": [],
        }

        # Stream queue for EngineCall → ParseStreamedResponse handoff
        self._streamQueue: asyncio.Queue[Any] = asyncio.Queue()

    def get(self, key: str, default: Any | None = None) -> Any | None:
        return self.runCtx.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        self.runCtx[key] = value
    
    def append(self, listKey: str, value: Any) -> None:
        lst = self.runCtx.setdefault(listKey, [])
        if isinstance(lst, list):
            lst.append(value)
        else:
            raise TypeError(f"runCtx['{listKey}'] is not a list")
    
    def fail(self, reason: str = "") -> None:
        self.set("error", reason)
        self._finish("failed")
    
    def cancel(self, reason: str = "") -> None:
        self.set("cancelReason", reason)
        self._finish("cancelled")
    
    # ----- Streaming -----
    
    async def streamPut(self, item: Any) -> None:
        await self._streamQueue.put(item)

    async def streamIter(self):
        while True:
            chunk = await self._streamQueue.get()
            if chunk is StopAsyncIteration:
                break
            yield chunk
    
    # ----- Session spawning helpers -----
    
    async def createHiddenSession(self, *, label: str = "") -> Session:
        """
        Hidden sessions are private to the creating mod by default.
        We construct an independent Session that mirrors ownerSession's bottom layers.
        It is not registered into a RuntimeInstance by design.
        """
        parent = self.ownerSession
        bottom = parent.memoryLayers[1:] # Drop txn from the top
        hidden = Session(
            kind=SessionKind.HIDDEN,
            ownerViewId=None,
            visibility=SessionVisibility.PRIVATE,
            sharedBottomLayers=bottom,
            savePath=parent.savePath,
        )
        # Let subscribers inspect the event (via the current run)
        self.runCtx["spawnedSession"] = {"kind": "hidden", "label": label, "session": hidden}
        for fn in self.pipeline.hiddenSessionSubscribers.values():
            maybe = fn(self)
            if asyncio.iscoroutine(maybe):
                await maybe
        return hidden
    
    async def createTemporarySession(self, *, label: str = "") -> Session:
        """
        Temporary sessions are public. Mods may opt-in listening after notification.
        We construct an independent Session that mirrors ownerSession's bottom layers.
        """
        parent = self.ownerSession
        bottom = parent.memoryLayers[1:] # Drop txn from the top
        temp = Session(
            kind=SessionKind.TEMPORARY,
            ownerViewId=None,
            visibility=SessionVisibility.PUBLIC,
            sharedBottomLayers=bottom,
            savePath=parent.savePath,
        )
        # Notify subscribers
        self.runCtx["spawnedSession"] = {"kind": "temporary", "label": label, "session": temp}
        for fn in self.pipeline.temporarySessionSubscribers.values():
            maybe = fn(self)
            if asyncio.iscoroutine(maybe):
                await maybe
        return temp
    
    # ----- Internals -----
    
    def _finish(self, status: Literal["succeeded", "failed", "cancelled"]) -> None:
        if self.status != "running":
            return
        self.status = status
        self.finishedTs = time.time()
        # Close streaming
        try:
            self._streamQueue.put_nowait(StopAsyncIteration)
        except Exception:
            pass
