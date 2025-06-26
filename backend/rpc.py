import httpx
from backend.rpc_websocket import registerRpc

@registerRpc("logMessage")
async def handleLogMessage(data: dict):
    from core.logger import getLogger
    logger = getLogger(str(data.get("modId", "?unknown?")), "frontend")
    level = data.get("level", "info")
    message = data.get("message", "")
    logFunc = getattr(logger, level, logger.info)
    logFunc(message)

class FrontendRPC:
    def __init__(self):
        self.frontendURL = "http://localhost:3000"

    async def callFrontendHook(self, hook, stage, data):
        print(f"Calling frontend hook '{hook.name}' for stage '{stage}' with data: {data}")
        payload = {
            "name": hook.name,
            "stage": stage,
            "data": data,
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.frontendURL}/frontend_execute_hook",
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            return result["data"]
