from backend.session import Session
from backend.temporary_session import TemporarySession
from backend.hidden_session import HiddenSession

import logging
logger = logging.getLogger(__name__)

class SessionManager:
    def __init__(self, mainSession: Session):
        self.mainSession = mainSession
        self.sessions: dict[str, Session] = {}

    def createTemporarySession(self, sessionId: str, clientId: int) -> Session:
        if not isinstance(sessionId, str) or len(sessionId.strip()) == 0:
            raise ValueError("sessionId must be a non-empty string!")
        if not isinstance(clientId, int):
            raise TypeError(f"clientId must be an integer, got {type(clientId)}")
        if self.sessions.get(sessionId):
            logger.warning("Session with id '%s' already exists!", sessionId)
        else:
            self.sessions[sessionId] = TemporarySession(sessionId, clientId)
        return self.sessions[sessionId]

    def createHiddenSession(self, sessionId: str, clientId: int) -> Session:
        if not isinstance(sessionId, str) or len(sessionId.strip()) == 0:
            raise ValueError("sessionId must be a non-empty string!")
        if not isinstance(clientId, int):
            raise TypeError(f"clientId must be an integer, got {type(clientId)}")
        if self.sessions.get(sessionId):
            logger.warning("Session with id '%s' already exists!", sessionId)
        else:
            self.sessions[sessionId] = HiddenSession(sessionId, clientId)
        return self.sessions[sessionId]

    def getSession(self, sessionId: str) -> Session:
        if not isinstance(sessionId, str) or len(sessionId.strip()) == 0:
            raise ValueError("sessionId must be a non-empty string!")
        session = self.sessions.get(sessionId)
        if session is None:
            raise ValueError(f"Session with id '{sessionId}' not found!")
        return session

    def destroySession(self, sessionId: str):
        if not isinstance(sessionId, str) or len(sessionId.strip()) == 0:
            raise ValueError("sessionId must be a non-empty string!")
        
        session = self.sessions.get(sessionId)
        if session is None:
            logger.warning("No session with id '%s' found!", sessionId)
        else:
            if session.sessionId == "main":
                logger.error("Cannot destroy main session!")
                return
            del self.sessions[sessionId]

            