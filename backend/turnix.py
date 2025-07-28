from typing import Any

from backend.session import Session
from backend.main_view import MainView
from backend.session_manager import SessionManager
from backend.view_manager import ViewManager
from backend.mod_manager import ModManager

import logging
logger = logging.getLogger(__name__)

class Turnix:
    def __init__(self, frontendServer) -> None:
        logger.debug("Initializing Turnix")
        self.frontendServer = frontendServer
        self.view = MainView()
        self.session = Session("main", self.view)
        self.sessionManager = SessionManager(mainSession=self.session)
        self.viewManager = ViewManager(mainView=self.view)
        self.modManager = ModManager()
    
    async def sendUserMessage(self, message: str):
        return await self.session.sendUserMessage(message)

    #async def import(self, modId: str) -> Any:
    #    await pass
