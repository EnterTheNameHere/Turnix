# backend/cli/main.py
from __future__ import annotations

import argparse

from backend.adapters.terminalAdapter import TerminalAdapter
from backend.pipeline.llamaCppServerProvider import LlamaCppServerConfig, LlamaCppServerProvider
from backend.pipeline.modelProvider import MockModelProvider, ModelProvider
from backend.pipeline.promptBudget import (
    PromptTokenBudgetConfig,
    PromptTokenBudgetMode,
    PromptTokenBudgetPolicy,
    makePromptTokenCounter,
)
from backend.runtime.runtimeHost import RuntimeHost

DEFAULT_APP_PACK_ID = "terminal-ai-chat"


def main(argv: list[str] | None = None) -> int:
    args = parseArgs(argv)
    modelProvider = makeModelProvider(args)
    promptTokenBudgetPolicy = makePromptTokenBudgetPolicy(args)

    host = RuntimeHost()
    try:
        host.start()
        appInstance = host.startAppInstance(
            DEFAULT_APP_PACK_ID,
            modelProvider=modelProvider,
            promptTokenBudgetPolicy=promptTokenBudgetPolicy,
        )
        TerminalAdapter(appInstance=appInstance).run()
        return 0
    finally:
        host.stop()


def parseArgs(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Turnix terminal AI chat runtime.")
    parser.add_argument(
        "--provider",
        choices=("mock", "llamacpp"),
        default="mock",
        help="Model provider to use (default: mock)",
    )
    parser.add_argument(
        "--llama-url",
        default="http://127.0.0.1:1234",
        help="Base URL for llama.cpp server (default: http://127.0.0.1:1234)",
    )
    parser.add_argument(
        "--llama-model",
        default="",
        help="Model name sent to llama.cpp. Empty string lets the server choose.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.6,
        help="Sampling temperature for llama.cpp provider (default: 0.6)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Maximum response tokens for llama.cpp and reserved response budget for prompt trimming (default: 512)",
    )
    parser.add_argument(
        "--prompt-budget-mode",
        choices=("none", "estimated"),
        default="none",
        help="Prompt token budget mode to use before model calls (default: none)",
    )
    parser.add_argument(
        "--context-size",
        type=int,
        default=None,
        help="Configured model context size for estimated prompt token budgeting",
    )
    parser.add_argument(
        "--prompt-safety-margin-tokens",
        type=int,
        default=128,
        help="Reserved prompt-budget safety margin in tokens (default: 128)",
    )
    parser.add_argument(
        "--estimated-characters-per-token",
        type=float,
        default=3.0,
        help="Estimated characters per token for local prompt token budgeting (default: 3.0)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Timeout in seconds for llama.cpp provider (default: 120)",
    )
    return parser.parse_args(argv)


def makeModelProvider(args: argparse.Namespace) -> ModelProvider:
    if args.provider == "mock":
        return MockModelProvider()

    return LlamaCppServerProvider(
        config=LlamaCppServerConfig(
            baseUrl=args.llama_url,
            model=args.llama_model,
            temperature=args.temperature,
            maxTokens=args.max_tokens,
            timeoutSeconds=args.timeout_seconds,
        ),
    )


def makePromptTokenBudgetPolicy(args: argparse.Namespace) -> PromptTokenBudgetPolicy:
    mode = PromptTokenBudgetMode(args.prompt_budget_mode)
    return PromptTokenBudgetPolicy(
        config=PromptTokenBudgetConfig(
            contextSize=args.context_size,
            reservedResponseTokenCount=args.max_tokens,
            safetyMarginTokenCount=args.prompt_safety_margin_tokens,
        ),
        tokenCounter=makePromptTokenCounter(
            mode,
            estimatedCharactersPerToken=args.estimated_characters_per_token,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
