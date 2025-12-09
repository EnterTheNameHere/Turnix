# backend/devtools/trace_capability.py
from __future__ import annotations

import asyncio
from typing import Any

from backend.app.globals import getTraceHub
from backend.rpc.api import exposeCapability, ICallContext, ISubscribeContext, ActiveSubscription

JsonDict = dict[str, Any]



def _matchesLevel(event: JsonDict, level: str | None) -> bool:
    if not level:
        return True
    recLevel = str(event.get("level", "")).lower()
    return recLevel == level.lower()



def _matchesTags(event: JsonDict, tags: list[str] | None) -> bool:
    if not tags:
        return True
    recTags = event.get("tags") or []
    if not isinstance(recTags, list):
        return False
    recTagsLower = {str(tag).lower() for tag in recTags}
    want = {str(tag).lower() for tag in tags}
    return not want.isdisjoint(recTagsLower)



def _matchesContext(event: JsonDict, ctxFilter: JsonDict | None) -> bool:
    """
    ctxFilter lets the caller filter by top-level context keys, e.g.:
    
    {
        "sessionId": "mainSession_...",
        "pipelineRunId": "pipelineRun_...",
        "viewId": "view_...",
        "clientId": "client_...",
    }
    
    All keys in ctxFilter must match exactly (string-equal) to pass.
    """
    if not ctxFilter:
        return True
    for key, expected in ctxFilter.items():
        if event.get(key) != expected:
            return False
    return True



async def listRecentTraces(
    _ctx: ICallContext, # for future auth/tenant scoping if needed
    *,
    limit: int = 200,
    level: str | None = None,
    tags: list[str] | None = None,
    contextFilter: JsonDict | None = None,
) -> JsonDict:
    """
    Returns a recent slice of trace events for devtools.
    
    • request/call
      capability: trace.stream@1
      path: recent
      args: {
          "limit"?: int,          # Max number of events to return
          "level"?: str,          # "debug" | "info" | "warning" | "error" | "trace"
          "tags"?: list[str],     # Event has at least one of these tags
          "contextFilter"?: dict, # Exact-match on keys like:
                                  #     sessionId, pipelineRunId, viewId, clientId, ...
      }
      
      Returns:
      {
          "events": [...],
          "total": int,           # Count after filtering
          "limit": int,           # Effective applied limit
          "more": bool,           # True if there were more (filtered) events than returned
      }
    """
    # Get snapshot via TraceHub. We do a subscribe/unsubscribe cycle purely to access
    # the buffered history without touching private attributes.
    hub = getTraceHub()
    snapshot, queue = hub.subscribe()
    hub.unsubscribe(queue)
    
    # Apply filters
    filtered: list[JsonDict] = []
    for event in snapshot:
        if not isinstance(event, dict):
            continue
        if not _matchesLevel(event, level):
            continue
        if not _matchesTags(event, tags):
            continue
        if not _matchesContext(event, contextFilter):
            continue
        filtered.append(event)
    
    total = len(filtered)
    effLimit = max(1, min(int(limit or 1), 2000)) # Hard cap to avoid exploding payloads.
    
    if total > effLimit:
        events = filtered[-effLimit:]
        more = True
    else:
        events = filtered
        more = False
    
    return {
        "events": events,
        "total": total,
        "limit": effLimit,
        "more": more,
    }



@exposeCapability("trace.stream@1")
class TraceCapability:
    """
    Turnix DevTools trace streaming capability (version 1).

    This capability exposes trace events from the backend TraceHub.
    It supports two operations:

    1. Streaming subscription:
       Sends an initial filtered snapshot of recent events and then streams
       new events as they occur.

    2. One-shot retrieval:
       Returns a filtered slice of recent events without opening a stream.

    RPC call
        path: "recent"

        Options:
            limit (int, optional): Maximum number of events to return.
            level (str, optional): Event level to match (e.g. "info", "debug").
            tags (list[str], optional): Match events that contain at least one tag.
            contextFilter (dict, optional): Exact match on context keys such as
                sessionId, pipelineRunId, viewId, clientId.

        Returns:
            dict with keys:
                events (list): Filtered event list.
                total (int): Number of events after filtering.
                limit (int): Effective limit applied.
                more (bool): True if more events existed than returned.

    Subscription
        path: "events"

        Sends:
            initial payload: { "events": [...snapshot...] }
            updates:        { "events": [...new events...] }

        The stream ends if the client unsubscribes or ctx.signal is set.

    Event shape
        Each event is a dict with fields such as:
            id (str)
            name (str)
            level (str)
            ts (int): timestamp in milliseconds
            spanId (str)
            traceId (str)
            tags (list)
            attrs (dict)

        Event contents are not modified. Filtering is non-destructive.
    """
    
    async def call(self, path: str, args: tuple[Any, ...], ctx: ICallContext) -> JsonDict:
        if path == "recent":
            opts = args[0] if args else {}
            if not isinstance(opts, dict):
                raise TypeError("trace.recent expects a dict as first argument")
            
            return await listRecentTraces(
                ctx,
                limit=opts.get("limit", 200),
                level=opts.get("level"),
                tags=opts.get("tags"),
                contextFilter=opts.get("contextFilter"),
            )
        
        raise ValueError(f"Unknown trace path: {path!r}")

    def subscribe(self, path: str, payload: JsonDict, ctx: ISubscribeContext) -> ActiveSubscription:
        if path != "events":
            raise ValueError(f"Unknown trace subscription path: {path!r}")
        
        opts = payload or {}
        level = opts.get("level")
        tags = opts.get("tags")
        contextFilter = opts.get("contextFilter")
        
        hub = getTraceHub()
        snapshot, queue = hub.subscribe()
        
        # Initial payload: filtered snapshot
        initialEvents: list[JsonDict] = []
        for event in snapshot:
            if not isinstance(event, dict):
                continue
            if not _matchesLevel(event, level):
                continue
            if not _matchesTags(event, tags):
                continue
            if not _matchesContext(event, contextFilter):
                continue
            initialEvents.append(event)
        
        async def _pump() -> None:
            try:
                while not ctx.signal.is_set():
                    event = await queue.get()
                    if not isinstance(event, dict):
                        continue
                    if not _matchesLevel(event, level):
                        continue
                    if not _matchesTags(event, tags):
                        continue
                    if not _matchesContext(event, contextFilter):
                        continue
                    try:
                        ctx.push({"events": [event]})
                    except Exception:
                        # Never let a broken client kill the stream loop
                        continue
            finally:
                hub.unsubscribe(queue)
        
        asyncio.create_task(_pump(), name=f"trace.stream:{ctx.id}")
        
        def _push(event: JsonDict) -> None:
            # Not used by _pump directly but required by ActiveSubscription.
            try:
                ctx.push(event)
            except Exception:
                pass
        
        def _onCancel() -> None:
            ctx.signal.set()
        
        return ActiveSubscription(
            push=_push,
            onCancel=_onCancel,
            initial={"events": initialEvents},
        )
