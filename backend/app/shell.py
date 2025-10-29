# backend/app/shell.py
from __future__ import annotations
import time
from typing import Any

from backend.core.ids import uuid_12
from backend.game.realm import GameRealm
from backend.sessions.session import Session
from backend.config.service import ConfigService
from backend.config.store import ConfigStore
from backend.app import state

__all__ = ["AppShell"]



class AppShell:
    """
    Launcher/menu state that exists when no GameRealm is active.
    Owns a 'shell' Session so we can run helper pipelines (LLM tips, save search, etc.)
    """
    def __init__(self, configService: ConfigService):
        self.id: str = uuid_12("shell_")
        self.createdTs: float = time.time()
        self.shellSession: Session = Session(kind="shell", sessionId=uuid_12("sh_"))
        self.recentSaves: list[dict[str, Any]] = []

        self.configService = configService
        self.globalConfig: ConfigStore = self.configService.globalStore

    def startNewGame(self, *, template: str | None = None) -> GameRealm:
        """Create a new GameRealm (menu → in-game transition)."""
        realm = GameRealm(
            configRegistry=self.configService.registry,
            globalConfigView=self.globalConfig, # shared global view
        )
        
        # Make it globally visible as the active game.
        state.GAME_REALM = realm

        # Optionally load settings/templates/difficulty, etc.
        if template:
            self._pushRecent({"kind": "new", "template": template, "ts": time.time()})
        
        # Preload mods/assets
        return realm
    
    def loadGame(self, *, savePath: str) -> GameRealm:
        realm = GameRealm(
            configRegistry=self.configService.registry,
            globalConfigView=self.globalConfig,
        )

        state.GAME_REALM = realm
        
        # Rehydrate
        return realm

    def returnToMenu(self, *, realm: GameRealm) -> None:
        """Tear down the running realm and return to shell mode."""
        # realm.save()
        realm.destroy(keepMain=False)

        # Clear active game pointer
        if state.GAME_REALM is realm:
            state.GAME_REALM = None

    def destroy(self) -> None:
        """Tear down AppShell resources (e.g., on process shutdown)"""
        try:
            self.shellSession.destroy()
        except Exception:
            pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "shellId": self.id,
            "createdTs": self.createdTs,
            "shellSessionId": self.shellSession.id,
            "recentSaves": self.recentSaves,
        }

    def _pushRecent(self, entry: dict[str, Any], keep: int = 10) -> None:
        self.recentSaves.insert(0, entry)
        if len(self.recentSaves) > keep:
            del self.recentSaves[keep:]
