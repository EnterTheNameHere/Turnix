from __future__ import annotations

from backend.llm.llmTypes import LlmQuery


def _llmSelection(ctx) -> tuple[str, str | None, dict[str, object]]:
    llm = ctx.config.get("llm")
    if not isinstance(llm, dict):
        raise ValueError("Application config llm must be an object.")
    provider = llm.get("provider")
    if type(provider) is not str or not provider:
        raise ValueError("Application config llm.provider must be a non-empty string.")
    model = llm.get("model")
    if model is not None and type(model) is not str:
        raise ValueError("Application config llm.model must be null or a string.")
    providerOptions = llm.get("providerOptions", {})
    if not isinstance(providerOptions, dict):
        raise ValueError("Application config llm.providerOptions must be an object.")
    return provider, model, providerOptions


def _estimateText(ctx, text: str) -> int:
    if type(text) is not str:
        raise TypeError("Token estimation text must be an exact built-in string.")
    provider, model, providerOptions = _llmSelection(ctx)
    return ctx.llm.estimateInputTokens(
        providerName=provider,
        model=model,
        providerOptions=providerOptions,
        query=LlmQuery(formatId="text/plain", payload=text),
    )


def _measure(ctx, payload):
    if not isinstance(payload, dict):
        raise ValueError("Token budget measurement requires an object payload.")
    text = payload.get("text")
    if type(text) is not str:
        raise TypeError("Token budget measurement requires a string text field.")
    return {"inputTokens": _estimateText(ctx, text)}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.tokenBudget@1", _measure)
