# first-party/mods/llm-prompt-builder/llm-prompt-builder.py
from __future__ import annotations

from typing import Any

from backend.app.globals import getActiveAppInstance
from backend.core.logger import getModLogger
from backend.pipeline.llmpipeline import LLMPipelineStages

logger = getModLogger("llm-prompt-builder")



class LLMPromptBuilder:
    """
    Smallest viable prompt builder:
      - input: list of MemItem-like (payload: {role, text})
      - output: OpenAI-style messages list
      - option: if flattenSystemToFirstUser=True → prepend system text into first user message
    """
    def __init__(self) -> None:
        # TODO: make Type → role mapping expendable.
        self.typeAlias = {
            "rules": "system",
            "characterBio": "system",
            "memorySummary": "system",
            "location": "assistant",   # narration
            "gmInstruction": "system", # or "assistant"
        }
    
    def _canonicalRole(self, role: str | None, itemType: str | None) -> str:
        role = (role or "").strip().lower()
        if role in {"system", "user", "assistant"}:
            return role
        # Aliases
        typ = (itemType or "").strip()
        if typ in self.typeAlias:
            return self.typeAlias[typ]
        # Default
        return "assistant"

    def buildMessages(
        self,
        items: list[Any],
        *,
        flattenSystemToFirstUser: bool = False,
    ) -> list[dict[str, str]]:
        """
        items: objects with .payload: {"role": str, "text": str, "type"?: str}
        Returns: [{"role": "...", "content": "..."}]
        """
        msgs: list[dict[str, str]] = []
        
        # 1) Collect and normalize
        for item in items:
            payload = getattr(item, "payload", None) or {}
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            role = self._canonicalRole(payload.get("role"), payload.get("type"))
            msgs.append({"role": role, "content": text})
        
        if not msgs:
            return []
        
        # 2) Optionally flatten system into the first user message
        if flattenSystemToFirstUser:
            systemChunks = [msg["content"] for msg in msgs if msg["role"] == "system"]
            if systemChunks:
                sysBlob = "\n".join(systemChunks).strip()
                # Find first user message. If none exists, prepend one
                for msg in msgs:
                    if msg["role"] == "user":
                        msg["content"] = (sysBlob + "\n\n" + msg["content"]).strip()
                        break
                else:
                    msgs.insert(0, {"role": "user", "content": sysBlob})
                # Remove original system messages
                msgs = [msg for msg in msgs if msg["role"] != "system"]

        return msgs

# ------------------------------------------------------------------ #
# Mod entry
# ------------------------------------------------------------------ #

def onLoad(ctx) -> None:
    """
    Registers a BuildPrompt stage handler with priority 0.
    Other mods can:
      - run earlier (negative priority) to modify memItems
      - run later (positive priority) to tweak engineRequest messages
    """
    appInstance = getActiveAppInstance()
    if appInstance is None or appInstance.mainSession is None:
        logger.error("llm-prompt-builder: no active appInstance or main session to attach to. Skipping...")
        return
    
    pipeline = appInstance.mainSession.pipeline
    builder = LLMPromptBuilder()
    
    async def buildPromptHandler(run, _payload):
        """
        Reads:
          - run.get("memItems"): list of MemItem (user + assistant history + current draft)
          - run.get("input")["options"] (optional builder flags)
        Writes:
          - run.set("engineRequest", {...}) with OpenAI-like "messages"
        """
        memItems = run.get("memItems") or []
        opts = (run.get("input") or {}).get("options") or {}
        flatten = bool(opts.get("flattenSystemToFirstUser", False))
        
        messages = builder.buildMessages(memItems, flattenSystemToFirstUser=flatten)
        
        # If someone already prepared an engineRequest, keep their non-conflicting fields.
        request = (run.get("engineRequest") or {}).copy()
        request.setdefault("model", "")
        request["messages"] = messages
        request.setdefault("stream", True)     # Default for streaming
        request.setdefault("temperature", 0.6) # Default sampler

        run.set("engineRequest", request)
        return {"promptStats": {"messages": len(messages)}}

    # Register at priority 0.
    pipeline.subscribeToStage(
        LLMPipelineStages.BuildPrompt,
        buildPromptHandler,
        priority=0,
    )
    
    logger.info("Builder registered.")
