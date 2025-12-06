# first-party/mods/llm-pipeline-cap/llm-pipeline-cap.py
from __future__ import annotations
import asyncio
from typing import Any

from backend.app.globals import getActiveAppInstance
from backend.core.logger import getModLogger
from backend.pipeline.llmpipeline import LLMPipelineStages
from backend.rpc.api import (
    listCapabilities,
    unregisterCapability,
    registerCapabilityInstance,
    ActiveSubscription,
)

logger = getModLogger("llm-pipeline-cap")
_CAP_NAME = "llm.pipeline@1"



def onLoad(ctx) -> None:
    """
    Public capability surface for llm.pipeline@1.
    
    - call("run", {threadId, userText, options}) → start LLMPipeline run
    - call("cancel", {runId}) → cancel run
    - subscribe("run", {runId}) → generic run events (messageDelta, runCompleted, etc.)
    - subscribe("stage", {runId, stage}) → lightweight stage stream (e.g. ParseStreamedResponse)
    """
    appInstance = getActiveAppInstance()
    if not appInstance or not appInstance.mainSession:
        logger.error("llm-pipeline-cap: missing appInstance.mainSession. Skipping init.")
        return
    
    pipeline = appInstance.mainSession.pipeline
    
    # Make sure we do not leave an old copy around (reloads).
    try:
        if _CAP_NAME in listCapabilities():
            unregisterCapability(_CAP_NAME)
    except Exception:
        logger.exception("llm-pipeline-cap: failed to unregister previous '%s'", _CAP_NAME)

    class _Cap:
        # ----- Internal helpers -----
        def _makePump(self, topic: str, ctx2):
            """
            Background pump from pipeline.events → ctx2.push(...).
            Used for subscribe('run', {...}) and similar.
            """
            async def pump() -> None:
                try:
                    async for ev in pipeline.events.subscribe(topic):
                        if getattr(ctx2, "signal", None) and ctx2.signal.is_set():
                            break
                        try:
                            ctx2.push(ev)
                        except Exception:
                            logger.debug(
                                "cap %s: ctx.push raised; stopping pump for topic '%s'", _CAP_NAME, topic
                            )
                            break
                except asyncio.CancelledError:
                    pass
                except Exception as err:
                    logger.exception("Pump error on topic '%s': %r", topic, err)
            
            task = asyncio.create_task(pump(), name=f"cap.{_CAP_NAME}.pump:{topic}")
            
            def onCancel() -> None:
                if task.done():
                    return
                task.cancel()
                
                async def _drain():
                    try:
                        await asyncio.wait_for(task, timeout=0.25)
                    except Exception:
                        pass
                
                asyncio.create_task(_drain())
            
            return {"initial": {"ok": True, "topic": topic}, "onCancel": onCancel}
        
        # ----- call(...) -----
        
        async def call(self, path: str, args: list[Any], _ctx) -> Any:
            if path == "run":
                payload = dict(args[0]) if (args and isinstance(args[0], dict)) else {}
                threadId = str(payload.get("threadId") or "default").strip() or "default"
                userText = str(payload.get("userText") or "").strip()
                if not userText:
                    raise ValueError("userText must be non-empty")
                
                opts = payload.get("options")
                if opts is not None and not isinstance(opts, dict):
                    raise TypeError("options must be a dict")
                
                # Basic guard. UI should enforce most limits.
                if len(userText) > 32_768: # TODO: Make configurable
                    raise ValueError("userText too long")
                
                logger.debug(
                    "Starting pipeline run for threadId='%s' (len=%d)",
                    threadId,
                    len(userText),
                )
                run = pipeline.startRun(
                    kind="chat",
                    initialInput={
                        "threadId": threadId,
                        "userText": userText,
                        "options": (opts or {}),
                    },
                )
                
                # Announce per-run channel bootstrap for generic subscribers
                asyncio.create_task(
                    pipeline.events.publish(
                        f"run:{run.runId}",
                        {
                            "kind": "runStarted",
                            "runId": run.runId,
                            "threadId": threadId,
                        },
                    )
                )
                return {"runId": run.runId}
            
            if path == "retry":
                # TODO: Implement re-run semantics
                return {"ok": False, "error": "retry not implemented"}
            
            if path == "cancel":
                payload = (args or [{}])[0]
                runId = str(payload.get("runId") or "").strip()
                if not runId:
                    raise ValueError("cancel requires {runId}")
                ok = bool(pipeline.cancel(runId))
                logger.debug("Cancel requested for runId='%s' -> %s", runId, ok)
                return {"ok": bool(ok)}
            
            raise ValueError(f"Unknown call path: {path}")
        
        # ----- subscribe(...) -----
        
        def subscribe(self, path: str, payload: Any, ctx2) -> dict[str, Any] | ActiveSubscription:
            if path == "run":
                if not isinstance(payload, dict):
                    raise TypeError("subscribe(run) payload must be a dict")
                runId = str(payload.get("runId") or "").strip()
                if not runId:
                    raise ValueError("subscribe(run) requires {runId}")
                return self._makePump(f"run:{runId}", ctx2)
            
            if path == "stage":
                if not isinstance(payload, dict):
                    raise TypeError("subscribe(stage) payload must be a dict")
                runId = str(payload.get("runId") or "").strip()
                stageName = str(payload.get("stage") or "").strip() or "ParseStreamedResponse"
                if not runId:
                    raise ValueError("subscribe(stage) requires {runId}")
                
                # Accept either enum name or enum value
                if (
                    stageName not in LLMPipelineStages.__members__
                    and stageName not in [stage.value for stage in LLMPipelineStages]
                ):
                    raise ValueError(f"Unknown stage '{stageName}'")
                
                stage = (
                    LLMPipelineStages[stageName]
                    if stageName in LLMPipelineStages.__members__
                    else LLMPipelineStages(stageName)
                )
                
                def _extractDeltaText(chunk: dict | None) -> str:
                    if not isinstance(chunk, dict) or not chunk:
                        return ""
                    if isinstance(chunk.get("textDelta"), str):
                        return chunk["textDelta"]
                    try:
                        chunk0 = (chunk.get("choices") or [{}])[0]
                        delta = chunk0.get("delta") or {}
                        txt = delta.get("content")
                        return txt if isinstance(txt, str) else ""
                    except Exception:
                        return ""
                
                def _onChunk(run, chunk):
                    if getattr(run, "runId", None) != runId:
                        return
                    deltaText = _extractDeltaText(chunk)
                    if not deltaText:
                        return
                    try:
                        ctx2.push(
                            {
                                "kind": "chunk",
                                "deltaText": deltaText,
                                "fields": {"status": "streaming"},
                            }
                        )
                    except Exception:
                        # Client disappeared. onCancel will tidy up.
                        pass
                
                subId = pipeline.subscribeToStage(
                    stage, _onChunk, priority=0, mode="perChunk"
                )
                
                async def _runPump():
                    """
                    Listen on run:<runId> and emit final done/error event.
                    """
                    try:
                        async for ev in pipeline.events.subscribe(f"run:{runId}"):
                            if getattr(ctx2, "signal", None) and ctx2.signal.is_set():
                                break
                            if ev.get("kind") == "runCompleted":
                                status = ev.get("status")
                                if status == "succeeded":
                                    ctx2.push(
                                        {
                                            "kind": "done",
                                            "fields": {"status": "final"},
                                        }
                                    )
                                else:
                                    ctx2.push(
                                        {
                                            "kind": "error",
                                            "fields": {"status": "error"},
                                            "error": ev.get("error") or ev.get("cancelReason"),
                                        }
                                    )
                                break
                    except Exception:
                        # Defensive: just stop streaming
                        pass
                
                pumpTask = asyncio.create_task(
                    _runPump(),
                    name=f"cap.{_CAP_NAME}.stage:{runId}:{stage.value}",
                )
                
                def onCancel():
                    try:
                        pipeline.unsubscribe(subId)
                    except Exception:
                        pass
                    if not pumpTask.done():
                        pumpTask.cancel()
                        asyncio.create_task(asyncio.sleep(0))
                
                return {
                    "initial": {"ok": True, "runId": runId, "stage": stage.value},
                    "onCancel": onCancel,
                }
            
            raise ValueError(f"Unknown subscribe path: {path}")

    registerCapabilityInstance(_CAP_NAME, _Cap())
    logger.info("Registered '%s' for LLMPipeline on main session", _CAP_NAME)
