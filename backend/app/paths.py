# backend/app/paths.py
from __future__ import annotations
from pathlib import Path



# Root directory structure constants
BACKEND_DIR = Path(__file__).resolve().parent.parent # backend/
ROOT_DIR = BACKEND_DIR.parent                        # repository root
WEBROOT = ROOT_DIR / "frontend"                      # static web frontend root
