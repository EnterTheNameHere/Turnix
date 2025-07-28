from backend.resilient_websocket import WebSocketRetryError
from backend.view import View
from backend.main_view import MainView

import logging
logger = logging.getLogger(__name__)

class ViewManager:
    def __init__(self, mainView: View):
        self.mainView = mainView
        self.views: dict[tuple[str, int], View] = {("main", 0): mainView}
 
    async def broadcast(self, data: dict):
        for _, view in self.views.items():
            try:
                await view.send(data)
            except WebSocketRetryError as e:
                logger.error(f"Failed to broadcast data to view '{view.key}': {e}")

        return

    def createView(self, viewId: str, clientId: int = 0) -> View:
        if not isinstance(viewId, str) or len(viewId.strip()) == 0:
            raise ValueError("viewId must be a non-empty string!")
        if not isinstance(clientId, int):
            raise TypeError(f"clientId must be an integer, got {type(clientId)}")
        if not self.views.get((viewId, clientId)):
            self.views[(viewId, clientId)] = View(viewId, clientId)
        else:
            logger.warning("View with id '%s' already exists!", viewId)
        return self.views[(viewId, clientId)]
    
    def getView(self, viewId: str, clientId: int = 0) -> View:
        if not isinstance(viewId, str) or len(viewId.strip()) == 0:
            raise ValueError("viewId must be a non-empty string!")
        if not isinstance(clientId, int):
            raise TypeError(f"clientId must be an integer!")
        view = self.views.get((viewId, clientId))
        if view is None:
            raise ValueError(f"View with id '{viewId}' not found!")
        return view

    def destroyView(self, viewId: str, clientId: int = 0):
        if not isinstance(viewId, str) or len(viewId.strip()) == 0:
            raise ValueError("viewId must be a non-empty string!")
        if not isinstance(clientId, int):
            raise TypeError("clientId must be an integer!")
        if not self.views.get((viewId, clientId)):
            logger.warning(f"No View ({viewId}, {clientId}) exists!")
        else:
            view = self.views[(viewId, clientId)]
            if isinstance(view, MainView):
                raise ValueError("Cannot destroy MainView!")
            del self.views[(viewId, clientId)]
