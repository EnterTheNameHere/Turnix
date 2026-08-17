from __future__ import annotations

import uuid

if not hasattr(uuid, "uuid7"):
    raise RuntimeError(
        "uuid7 not available. Python 3.14 or higher is required.",
    )
