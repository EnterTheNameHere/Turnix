import os
import threading
import socket
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import logging
logger = logging.getLogger(__name__)

class CustomRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, frontendServer: "FrontendServer", **kwargs):
        self.frontendServer = frontendServer
        super().__init__(*args, **kwargs)

    def translate_path(self, requestedPath: str) -> str:
        from urllib.parse import urlparse, unquote
        from pathlib import Path

        parsed_path = urlparse(requestedPath)
        unquoted_path = unquote(parsed_path.path)
        unquoted_path = unquoted_path.replace("\\", "/").lstrip("/")

        for route_prefix, target_directory in self.frontendServer.routedDirectories.items():
            # Normalize route prefix
            prefix = route_prefix.strip("/")
            
            if prefix and unquoted_path.startswith(prefix + "/"):
                relative_subpath = unquoted_path[len(prefix) + 1:]
                
                # Resolve base directory (absolute = use directly, relative = resolve from baseDirectory)
                base = Path(target_directory)
                if not base.is_absolute():
                    base = Path(self.frontendServer.baseDirectory) / base
                
                final_path = (base / relative_subpath).resolve()

                # Ensure resolve path is still inside the routed directory
                if not str(final_path).startswith(str(base.resolve())):
                    raise PermissionError(f"🚫 Path traversal attempt outside of routed directory: '{final_path}'")

                return str(final_path)

        # Fallback: use default directory inside baseDirectory
        base = Path(self.frontendServer.baseDirectory) / self.frontendServer.defaultDirectory
        final_path = (base / unquoted_path).resolve()

        if not str(final_path).startswith(str(base.resolve())):
            raise PermissionError(f"🚫 Path traversal attempt outside of default directory: '{final_path}'")

        return str(final_path)

class FrontendServer:
    def __init__(self, address="localhost", port=3000, directory='frontend', modsDirectory='mods'):
        self.address = address
        self.port = port
        self.baseDirectory = os.getcwd()
        self.defaultDirectory = directory
        # url prefix: directory
        self.routedDirectories = {
            "/mods/": modsDirectory,
        }
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.running = False

    def start(self):
        if self.running:
            logger.info(f"Already running at http://localhost:{self.port}")
            return
        
        try:
            handler_class = partial(CustomRequestHandler, frontendServer=self)
            self.server = ThreadingHTTPServer((self.address, self.port), handler_class)
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
