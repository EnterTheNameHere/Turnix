from __future__ import annotations

import argparse

from backend.adapters.terminalAdapter import TerminalAdapter
from backend.pipeline.llamaCppServerProvider import LlamaCppServerConfig, LlamaCppServerProvider
from backend.pipeline.modelProvider import MockModelProvider, ModelProvider
from backend.runtime.runtimeHost import RuntimeHost


DEFAULT_APP_PACK_ID = "terminal-ai-chat"


def main(argv: list[str] | None = None) -> int:
    args = parseArgs(argv)
    modelProvider = makeModelProvider(args)
    
    host = RuntimeHost()
    try:
        host.start()
        appInstance = host.startAppInstance(DEFAULT_APP_PACK_ID, modelProvider=modelProvider)
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
        default="http://localhost:1234",
        help="Base URL for llama.cpp server (default: http://localhost:1234)",
    )
    parser.add_argument(
        "--llama-model",
        default="",
        help="Model name sent to llama.cpp. Empty string lets the server choose."
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
        help="Maximum number of tokens for llama.cpp provider (default: 512)",
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


if __name__ == "__main__":
    raise SystemExit(main())
