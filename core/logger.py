import logging
import asyncio

class JSLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.queue = []
        self._ready = False # Set True when pushEvent is available

    def emit(self, record: logging.LogRecord):
        logData = {
            "level": record.levelname.lower(),
            "source": record.name,
            "message": self.format(record)
        }

        # Fire and forget to JS
        try:
            if self._ready:
                from backend.rpc_websocket import pushEvent
                asyncio.create_task(pushEvent("log", logData))
            else:
                self.queue.append(logData)
        
        except RuntimeError:
            # Ignore errors when the event loop isn't running
            pass
        
    def flushQueue(self):
        if self._ready:
            from backend.rpc_websocket import pushEvent
            for record in self.queue:
                asyncio.create_task(pushEvent("log", record))
            self.queue.clear()

    def setReady(self, ready = True):
        self._ready = ready
        self.flushQueue()

__jsHandler = JSLogHandler()

def getJSLogHandler():
    return __jsHandler

def configureLogging():
    global __jsHandler

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s: [%(name)-12s] %(message)s")

    streamHandler = logging.StreamHandler()
    streamHandler.setFormatter(formatter)

    fileHandler = logging.FileHandler("backend.log", mode="w", encoding="utf-8")
    fileHandler.setFormatter(formatter)

    jsHandler = getJSLogHandler()
    jsHandler.setFormatter(formatter)

    root.addHandler(streamHandler)
    root.addHandler(fileHandler)
    root.addHandler(jsHandler)

def getLogger(name: str, side: str = ""):
    if side == "":
        return logging.getLogger(name)
    else:
        return logging.getLogger(f"{side}.{name}")

def getModLogger(modId: str):
    return logging.getLogger(f"mods.{modId}")

def getProfilerLogger():
    return logging.getLogger("profiler")
