import os
import threading
import socket
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import logging
logger = logging.getLogger(__name__)

class FrontendServer:
    def __init__(self, port=3000, directory='frontend'):
        self.port = port
        self.directory = directory
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.running = False

    def start(self):
        if self.running:
            logger.info(f"Already running at http://localhost:{self.port}")
            return
        
        try:
            handler = partial(SimpleHTTPRequestHandler, directory=self.directory) # Sets cwd only for HTTP server
            self.server = ThreadingHTTPServer(("localhost", self.port), handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.running = True
            logger.info(f"Server running. Main view at http://localhost:{self.port}")
        except OSError as e:
            logger.error(f"Failed to start server. Error: {e}")
            self.running = False
    
    def stop(self):
        if self.server and self.running:
            logger.info(f"Shutting down...")
            self.server.shutdown()
            self.server.server_close()
            if self.thread is not None:
                self.thread.join()
            self.running = False
            logger.info("Stopped.")
    
    def restart(self):
        logger.info("Restarting server...")
        self.stop()
        self.start()
    
    def isRunning(self) -> bool:
        return self.running and self._isPortOpen()

    def _isPortOpen(self) -> bool:
        try:
            with socket.create_connection(("localhost", self.port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            return False
