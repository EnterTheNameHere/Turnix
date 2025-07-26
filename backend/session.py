from backend.llmpipeline import LLMPipeline
from core.observer_bus import ObserverBus

import logging
logger = logging.getLogger(__name__)

class Session:
    def __init__(self, sessionId: str, view):
        self.sessionId = sessionId
        self.view = view
        self.observerBus = ObserverBus(view, self)
        self.pipeline = None
    
    async def registerFrontendHook(self, data):
        if "location" not in data:
            data["location"] = "frontend"
        if self.observerBus is not None:
            await self.observerBus.register(data)
        else:
            raise Exception("Cannot register frontend hook because ObserverBus is None.")

    async def sendUserMessage(self, data):
        logger.info(f"Session {self.sessionId} received user message: {data.get('text')}")
        if self.pipeline is None:
            self.pipeline = LLMPipeline(self.observerBus)
        try:
            await self.pipeline.process(self.sessionId, data.get("text"))
        except Exception:
            logger.exception(f"Error running LLM pipeline for session '{self.sessionId}'.")
