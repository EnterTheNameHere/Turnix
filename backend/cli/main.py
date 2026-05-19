from __future__ import annotations

from backend.adapters.terminalAdapter import TerminalAdapter
from backend.runtime.runtimeHost import RuntimeHost


DEFAULT_APP_PACK_ID = "terminal-ai-chat"


def main() -> int:
    host = RuntimeHost()
    try:
        host.start()
        appInstance = host.startAppInstance(DEFAULT_APP_PACK_ID)
        TerminalAdapter(appInstance=appInstance).run()
        return 0
    finally:
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
