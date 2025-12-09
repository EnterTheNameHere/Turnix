# first-party/mods/chat-history/chat-history.py
from __future__ import annotations
import asyncio
import time

from backend.app.globals import getActiveAppInstance
from backend.core.logger import getModLogger
from backend.pipeline.llmpipeline import LLMPipelineStages
from backend.memory.memory_layer import QueryItem as MemItem
from backend.core.ids import uuid_12

logger = getModLogger("chat-history")



# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _mkUserMemItem(text: str, *, threadId: str) -> MemItem:
    oid = uuid_12("user_")
    return MemItem(
        id=oid,
        kind="userMessage",
        payload={"role": "user", "text": text, "threadId": threadId, "status": "final"},
        path=f"session.chat.{threadId}.{oid}",
        originLayer="session", # Resolves to session:<id>
        meta={"threadId": threadId, "ts": int(time.time() * 1000)},
    )



def _mkAssistantMemItem(*, threadId: str, status: str = "draft") -> MemItem:
    oid = uuid_12("assistant_")
    return MemItem(
        id=oid,
        kind="assistantMessage",
        payload={"role": "assistant", "text": "", "threadId": threadId, "status": status},
        path=f"session.chat.{threadId}.{oid}",
        originLayer="session", # Resolves to session:<id>
        meta={"threadId": threadId, "ts": int(time.time() * 1000)},
    )



def _takeTail(history: list[MemItem], *, maxUser: int = 6, maxAssistant: int = 6) -> list[MemItem]:
    # Simple tail-window: walk from end, keep up to N per role
    kept: list[MemItem] = []
    uLeft, aLeft = maxUser, maxAssistant
    for item in reversed(history):
        role = (item.payload or {}).get("role")
        if role == "user" and uLeft > 0:
            kept.append(item)
            uLeft -= 1
        elif role == "assistant" and aLeft > 0:
            kept.append(item)
            aLeft -= 1
        # Stop early if both exhausted
        if uLeft <= 0 and aLeft <= 0:
            break
    return list(reversed(kept))



from backend.sessions.session import Session
def _loadThreadFromMemory(session: Session, threadId: str) -> list[MemItem]:
    """
    Linear read from layered memory under 'session.chat.<threadId>.*'.
    For DictMemoryLayer this is cheap (single map).
    """
    res: list[MemItem] = []
    # We only have convenient per-key get(). Enumerate keys from the session layer if possible.
    layer = session.sessionMemory
    if hasattr(layer, "data"):
        prefix = f"session.chat.{threadId}."
        for key, versions in getattr(layer, "data", {}).items():
            if not versions:
                continue
            # Keys are stored without the 'session.' prefix inside DictMemoryLayer.
            # memory.savePersistent() writes with clean key. We staged with LayeredMemory.save(txn),
            # so after commit they'll exist in the session layer under 'chat.<threadId>.<oid>'.
            # Map both possibilities for safety:
            if key.startswith(f"chat.{threadId}.") or key.startswith(prefix):
                obj = versions[-1]
                # Normalize to MemItem (MemoryObject is compatible)
                res.append(MemItem(
                    id=obj.id,
                    kind=getattr(obj, "kind", (obj.payload or {}).get("kind", "message")),
                    payload=obj.payload,
                    path=obj.path,
                    originLayer=obj.originLayer,
                    uuidStr=obj.uuidStr,
                    version=obj.version,
                    meta=obj.meta,
                ))
    # Sort by ts, fallback to id
    res.sort(key=lambda message: (message.meta.get("ts", 0) if isinstance(message.meta, dict) else 0, message.id))
    return res



# ------------------------------------------------------------------ #
# Mod entry
# ------------------------------------------------------------------ #

def onLoad(ctx) -> None:
    """
    - Grabs main session
    - Hooks LLMPipeline stages to:
        • PrepareInput: normalize inputs
        • BuildQueryItems: load history from session memory + add current user item (txn)
        • ParseStreamedResponse: append deltas into assistant draft (txn)
        • ParseResponse/UpdateQueryItems: finalize assistant and stage final payload (txn)
        • Finalize: publish run lifecycle events and clean up on failure/cancel
      LLMPipeline will call Session.saveMemory() on success, which commits txn → session
      and persists per your MemorySaveManager policy.
    """
    services = getattr(ctx, "_services", {})
    
    # Required pieces
    llm = services.get("llm")
    appInstance = getActiveAppInstance()
    mainSession = appInstance.mainSession
    
    if llm is None or mainSession is None:
        missing = []
        if llm is None:
            missing.append("SERVICES['llm']")
        if mainSession is None:
            missing.append("SERVICES['mainSession'] or SERVICES['appInstance'].mainSession")
        logger.error("missing %s; skipping init", " & ".join(missing))
        return
    
    pipeline = mainSession.pipeline
    
    # ----- Stage: PrepareInput -----
    def stagePrepareInput(run, _payload=None):
        inp = run.get("input", {})
        threadId = str(inp.get("threadId") or "default").strip() or "default"
        userText = str(inp.get("userText") or "").strip()
        if not userText:
            run.fail("emptyUserText")
            return
        run.set("threadId", threadId)
        run.set("userText", userText)
    
    pipeline.subscribeToStage(LLMPipelineStages.PrepareInput, stagePrepareInput)

    # ----- Stage: BuildQueryItems (history + current user) -----
    def stageBuildItems(run, _payload=None):
        threadId = run.get("threadId")
        userText = run.get("userText", "").strip()
        
        # Load prior conversation from persistent session memory
        history: list[MemItem] = _loadThreadFromMemory(mainSession, threadId)
        short = _takeTail(history, maxUser=6, maxAssistant=6)
        
        # Current user message → staged in txn
        userItem = _mkUserMemItem(userText, threadId=threadId)
        mainSession.memory.save(userItem) # Stage to txn. Commit happens at pipeline success
        
        # Assistant draft (to be filled by streaming)
        assistantItem = _mkAssistantMemItem(threadId=threadId, status="streaming")
        mainSession.memory.save(assistantItem)
        
        queryItems: list[MemItem] = [*short, userItem, assistantItem]
        run.set("memItems", queryItems)
        run.set("assistantOid", assistantItem.id)
        run.set("userOid", userItem.id)
        
        # Inform UI about new items appended to the thread
        headers = {
            userItem.id: {"role": "user", "preview": (userText[:400] if userText else ""), "status": "final"},
            assistantItem.id: {"role": "assistant", "preview": "", "status": "streaming"},
        }
        delta = {
            "kind": "threadDelta",
            "op": "insert",
            "at": len(history), # Append at the end
            "oids": [userItem.id, assistantItem.id],
            "headers": headers,
        }
        # Publish to the live thread channel
        asyncio.create_task(pipeline.events.publish(f"thread:{threadId}", delta))
        
    pipeline.subscribeToStage(LLMPipelineStages.BuildQueryItems, stageBuildItems)
    
    # ----- Stage: ParseStreamedResponse (update assistant draft) -----
    async def stageParseStreamedResponse(run, chunk):
        assistantOid = run.get("assistantOid")
        threadId = run.get("threadId")
        if not assistantOid or not threadId:
            return
        
        
        delta = (chunk or {}).get("textDelta")
        # Also accept OpenAI-like events if the driver forwards raw chunks
        if delta is None and isinstance(chunk, dict) and "choices" in chunk:
            delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
        if not delta:
            return
        
        # Accumulate in run context (used by ParseResponse later)
        accumulatedText = run.get("assistantTextAccumulated") or ""
        run.set("assistantTextAccumulated", accumulatedText + delta)
        run.append("messagesDelta", {"oid": assistantOid, "textDelta": delta})
        
        # Live UI streaming
        msgDelta = {"kind": "messageDelta", "oid": assistantOid, "textDelta": delta}
        await pipeline.events.publish(f"thread:{threadId}", msgDelta)
        await pipeline.events.publish(f"run:{run.runId}", msgDelta)
        
    pipeline.subscribeToStage(
        LLMPipelineStages.ParseStreamedResponse,
        stageParseStreamedResponse,
        priority=0,
        mode="perChunk"
    )
    
    # ----- Stage: ParseResponse (finalize text) -----
    def stageParseResponse(run, _payload):
        finalText = run.get("assistantTextAccumulated") or ""
        run.set("engineResponse", {"text": finalText})
    
    pipeline.subscribeToStage(LLMPipelineStages.ParseResponse, stageParseResponse)
    
    # ----- Stage: UpdateQueryItems (persist assistant final) -----
    def stageUpdateItems(run, _payload=None):
        threadId = run.get("threadId")
        assistantOid = run.get("assistantOid")
        resp = run.get("engineResponse") or {}
        finalText = resp.get("text", "")
        
        # Create a final assistant message object (new version under same path/oid)
        finalItem = MemItem(
            id=assistantOid,
            kind="assistantMessage",
            payload={"role": "assistant", "text": finalText, "threadId": threadId, "status": "final"},
            path=f"session.chat.{threadId}.{assistantOid}",
            originLayer="session",
            meta={"threadId": threadId, "ts": int(time.time() * 1000)},
        )
        mainSession.memory.save(finalItem) # Staged. Commit+persist happens after LLMPipeline completes
        # Tell UI the message is finalized (full text + status)
        finalDelta = {
            "kind": "messageDelta",
            "oid": assistantOid,
            "text": finalText,
            "fields": {"status": "final"}
        }
        asyncio.create_task(pipeline.events.publish(f"thread:{threadId}", finalDelta))
        asyncio.create_task(pipeline.events.publish(f"run:{run.runId}", finalDelta))
    
    pipeline.subscribeToStage(LLMPipelineStages.UpdateQueryItems, stageUpdateItems)
    
    # ----- Stage: Finalize (publish lifecycle + cleanup on failure/cancel) -----
    def stageFinalize(run, _payload=None):
        status = run.status # "running" never reaches here
        threadId = run.get("threadId")
        userOid = run.get("userOid")
        assistantOid = run.get("assistantOid")
        
        # On failure/cancel, the txn is rolled back by the pipeline.
        # Reflect that in UI by removing the optimistic inserts and marking assistant draft as error.
        if status in ("failed", "cancelled"):
            if assistantOid:
                asyncio.create_task(pipeline.events.publish(
                    f"thread:{threadId}",
                    {"kind": "messageDelta", "oid": assistantOid, "fields": {"status": "error"}}
                ))
            if userOid and assistantOid:
                asyncio.create_task(pipeline.events.publish(
                    f"thread:{threadId}",
                    {"kind": "threadDelta", "op": "remove", "oids": [userOid, assistantOid]}
                ))
        
        # Always publish a per-run completion notification
        payload = {
            "kind": "runCompleted",
            "runId": run.runId,
            "threadId": threadId,
            "status": status,
        }
        # Bubble through error/cancel reason if present
        err = run.get("error")
        if err:
            payload["error"] = err
        cancel = run.get("cancelReason")
        if cancel:
            payload["cancelReason"] = cancel
        asyncio.create_task(pipeline.events.publish(f"run:{run.runId}", payload))
    
    pipeline.subscribeToStage(LLMPipelineStages.Finalize, stageFinalize)
