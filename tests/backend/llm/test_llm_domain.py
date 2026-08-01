# file: tests/backend/llm/test_llm_domain.py
from __future__ import annotations
from backend.llm.llmQueryItem import LlmQueryItemId

from typing import TYPE_CHECKING

import pytest

from backend.llm.llmComponents import LlmStageComponentRegistry
from backend.llm.llmEchoProvider import LlmEchoProvider
from backend.llm.llmHookRegistry import LlmHookRegistry
from backend.llm.llmProcessingPipeline import LlmProcessingPipeline
from backend.llm.llmProcessingPipelineRun import LlmProcessingRequest, LlmProcessingRunId
from backend.llm.llmProcessingPipelineStages import BUILD_QUERY_ITEMS, STREAM_EVENT
from backend.llm.llmProcessingPipelineTransactions import LlmPipelineTransaction
from backend.llm.llmProviderRegistry import LlmStreamProviderRegistry
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

if TYPE_CHECKING:
    from backend.llm.llmStageContext import LlmStageContext


class StubTransaction(LlmPipelineTransaction):
    """Tracks one test pipeline transaction."""

    def __init__(self) -> None:
        """Initializes an active test transaction."""
        self.committed = False
        self.rolledBack = False

    def commit(self) -> None:
        """Commits the test transaction."""
        if self.committed or self.rolledBack:
            raise RuntimeError("Transaction has already completed.")

        self.committed = True

    def rollback(self) -> None:
        """Rolls back the test transaction."""
        if self.committed or self.rolledBack:
            raise RuntimeError("Transaction has already completed.")

        self.rolledBack = True


class StubTransactionManager:
    """Creates and retains the latest test transaction."""

    def __init__(self) -> None:
        """Initializes the test transaction manager."""
        self.lastTransaction: StubTransaction | None = None

    def beginLlmPipelineTransaction(
        self,
        *,
        runId: LlmProcessingRunId,
    ) -> StubTransaction:
        """Begins one test pipeline transaction."""
        del runId

        transaction = StubTransaction()
        self.lastTransaction = transaction
        return transaction


def test_pipeline_builds_prompt_streams_and_commits() -> None:
    providers = LlmStreamProviderRegistry()
    providers.register(
        providerName="echo",
        ownerId=PackCodeEntryInstanceId.new(),
        provider=LlmEchoProvider(),
    )

    hooks = LlmHookRegistry()

    def addPromptItem(ctx: LlmStageContext) -> None:
        ctx.addQueryItem(
            itemId=LlmQueryItemId("input"),
            content=str(ctx.rawInput["text"]),
            importance=100,
            mandatory=True,
        )

    def uppercase(ctx: LlmStageContext) -> None:
        ctx.replaceCurrentStreamText(ctx.currentStreamText.upper())

    hooks.register(
        stageId=BUILD_QUERY_ITEMS,
        ownerId=PackCodeEntryInstanceId.new(),
        handler=addPromptItem,
        activationOrder=0,
    )
    hooks.register(
        stageId=STREAM_EVENT,
        ownerId=PackCodeEntryInstanceId.new(),
        handler=uppercase,
        activationOrder=1,
    )

    transactions = StubTransactionManager()
    pipeline = LlmProcessingPipeline(
        providerRegistry=providers,
        hookRegistry=hooks,
        transactionManager=transactions,
        componentRegistry=LlmStageComponentRegistry(),
    )

    result = pipeline.run(
        LlmProcessingRequest(
            purposeId="test.echo",
            providerName="echo",
            rawInput={"text": "hello"},
        ),
    )

    assert result.callRequest.prompt == "hello"
    assert result.response.rawText == "hello"
    assert result.response.processedText == "HELLO"

    transaction = transactions.lastTransaction
    assert transaction is not None
    assert transaction.committed
    assert not transaction.rolledBack


def test_pipeline_rolls_back_when_hook_fails() -> None:
    providers = LlmStreamProviderRegistry()
    providers.register(
        providerName="echo",
        ownerId=PackCodeEntryInstanceId.new(),
        provider=LlmEchoProvider(),
    )

    hooks = LlmHookRegistry()

    def fail(ctx: LlmStageContext) -> None:
        del ctx
        raise RuntimeError("failure")

    hooks.register(
        stageId=BUILD_QUERY_ITEMS,
        ownerId=PackCodeEntryInstanceId.new(),
        handler=fail,
        activationOrder=0,
    )

    transactions = StubTransactionManager()
    pipeline = LlmProcessingPipeline(
        providerRegistry=providers,
        hookRegistry=hooks,
        transactionManager=transactions,
        componentRegistry=LlmStageComponentRegistry(),
    )

    with pytest.raises(RuntimeError):
        pipeline.run(
            LlmProcessingRequest(
                purposeId="test.failure",
                providerName="echo",
                rawInput=None,
            ),
        )

    transaction = transactions.lastTransaction
    assert transaction is not None
    assert not transaction.committed
    assert transaction.rolledBack

