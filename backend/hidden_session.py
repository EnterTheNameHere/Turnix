from backend.session import Session

import logging
logger = logging.getLogger(__name__)

class HiddenSession(Session):
    def __init__(self, sessionId: str, clientId: int):
        super().__init__(sessionId, clientId)
        