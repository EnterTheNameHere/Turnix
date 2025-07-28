from backend.view import View

import logging
logger = logging.getLogger(__name__)

class MainView(View):
    def __init__(self):
        super().__init__("main", 0)
