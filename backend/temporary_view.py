from view import View

class TemporaryView(View):
    def __init__(self, sessionId: str, clientId: int = 0) -> None:
        super().__init__(sessionId, clientId)
