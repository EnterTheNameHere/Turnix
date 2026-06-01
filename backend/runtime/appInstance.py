# backend/runtime/appInstance.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.memory.messageStore import MessageStore
from backend.pipeline.chatPipeline import ChatPipeline, ChatPipelineResult
from backend.pipeline.modelProvider import MockModelProvider, ModelProvider
from backend.pipeline.promptBudget import PromptTokenBudgetPolicy
from backend.pipeline.promptBuilder import PromptBuilder


class AppInstanceState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass(frozen=True)
class AppInstanceIdentity:
    """Runtime identity for one live AppInstance."""

    appPackId: str
    appInstanceId: str


class AppInstance:
    """Live AppInstance for the terminal chat runtime."""

    def __init__(
        self,
        *,
        identity: AppInstanceIdentity,
        modelProvider: ModelProvider | None = None,
        promptTokenBudgetPolicy: PromptTokenBudgetPolicy | None = None,
    ) -> None:
        self.identity = identity
        self.state = AppInstanceState.CREATED
        self.messageStore = MessageStore()
        self.promptBuilder = PromptBuilder()
        self.modelProvider = modelProvider or MockModelProvider()
        self.promptTokenBudgetPolicy = promptTokenBudgetPolicy or PromptTokenBudgetPolicy()
        self.chatPipeline = ChatPipeline(
            messageStore=self.messageStore,
            promptBuilder=self.promptBuilder,
            modelProvider=self.modelProvider,
            promptTokenBudgetPolicy=self.promptTokenBudgetPolicy,
        )

    @property
    def appPackId(self) -> str:
        return self.identity.appPackId

    @property
    def appInstanceId(self) -> str:
        return self.identity.appInstanceId

    def start(self) -> None:
        if self.state == AppInstanceState.RUNNING:
            return
        if self.state == AppInstanceState.STOPPED:
            raise RuntimeError(f"AppInstance '{self.appInstanceId}' cannot be restarted after stop")
        self.state = AppInstanceState.RUNNING

    def stop(self) -> None:
        if self.state == AppInstanceState.STOPPED:
            return
        self.state = AppInstanceState.STOPPED

    def handleUserMessage(self, userText: str) -> ChatPipelineResult:
        if self.state != AppInstanceState.RUNNING:
            raise RuntimeError(f"AppInstance '{self.appInstanceId}' is not running")
        return self.chatPipeline.runUserMessage(userText)
