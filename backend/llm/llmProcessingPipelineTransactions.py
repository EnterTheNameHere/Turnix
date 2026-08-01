# file: backend/llm/llmProcessingPipelineTransactions.py ; version: 2
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.llm.llmProcessingPipelineRun import LlmProcessingRunId

__all__: list[str] = [
    "LlmPipelineTransaction",
    "LlmPipelineTransactionManager",
]


class LlmPipelineTransaction(Protocol):
    """
    Defines a child transaction spanning one complete pipeline run.

    Exactly one terminal operation may succeed. Implementations must reject
    commit() or rollback() after the transaction has already been committed or
    rolled back.
    """

    def commit(self) -> None:
        """Commits the transaction and enters its terminal state."""
        ...

    def rollback(self) -> None:
        """Rolls back the transaction and enters its terminal state."""
        ...


class LlmPipelineTransactionManager(Protocol):
    """Defines creation of child transactions for LLM processing runs."""

    def beginLlmPipelineTransaction(
        self,
        *,
        runId: LlmProcessingRunId,
    ) -> LlmPipelineTransaction:
        """Begins one child transaction for the processing run."""
        ...
