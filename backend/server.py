# backend/server.py
from __future__ import annotations

import logging

from backend.app.factory import createApp

# Basic logging setup, before we can initialize config service to read logging config
logging.basicConfig(level=logging.INFO) # Set to INFO temporarily
logger = logging.getLogger(__name__)
logger.info("Basic logging initiated...")


app = createApp()
