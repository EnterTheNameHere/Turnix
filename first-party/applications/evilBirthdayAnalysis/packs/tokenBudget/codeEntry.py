from __future__ import annotations

import json
import urllib.request as urlRequest
from urllib.error import HTTPError, URLError


def _baseUrl(ctx) -> str:
    llamaCpp = ctx.config.get("llamaCpp")
    if not isinstance(llamaCpp, dict):
        raise ValueError("Application config llamaCpp must be an object.")
    baseUrl = llamaCpp.get("baseUrl")
    if type(baseUrl) is not str or not baseUrl.strip():
        host = llamaCpp.get("host", "127.0.0.1")
        port = llamaCpp.get("port", 8080)
        if type(host) is not str or not host or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("llamaCpp must provide baseUrl or valid host/port values.")
        baseUrl = f"http://{host}:{port}"
    return baseUrl.rstrip("/")


def _timeoutSeconds(ctx) -> float:
    llm = ctx.config.get("llm")
    if not isinstance(llm, dict):
        raise ValueError("Application config llm must be an object.")
    providerOptions = llm.get("providerOptions", {})
    if not isinstance(providerOptions, dict):
        raise ValueError("llm.providerOptions must be an object.")
    value = providerOptions.get("timeoutSeconds", 120.0)
    if type(value) not in {int, float} or float(value) <= 0:
        raise ValueError("llm.providerOptions.timeoutSeconds must be positive when configured.")
    return float(value)


def _postJson(ctx, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
    url = f"{_baseUrl(ctx)}{endpoint}"
    request = urlRequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlRequest.urlopen(request, timeout=_timeoutSeconds(ctx)) as response:
            raw = response.read()
    except HTTPError as err:
        raise RuntimeError(f"llama.cpp returned HTTP {err.code} while measuring tokens at {endpoint}.") from err
    except (URLError, TimeoutError) as err:
        raise RuntimeError(f"Failed communicating with llama.cpp while measuring tokens at {endpoint}.") from err
    try:
        decoded = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise RuntimeError(f"llama.cpp returned an invalid JSON token-measurement response from {endpoint}.") from err
    if not isinstance(decoded, dict):
        raise RuntimeError(f"llama.cpp returned a non-object token-measurement response from {endpoint}.")
    return decoded


def _estimateText(ctx, text: str) -> int:
    if type(text) is not str:
        raise TypeError("Token estimation text must be an exact built-in string.")

    templated = _postJson(
        ctx,
        "/apply-template",
        {"messages": [{"role": "user", "content": text}]},
    )
    prompt = templated.get("prompt")
    if type(prompt) is not str:
        raise RuntimeError("llama.cpp /apply-template response does not contain a string prompt.")

    tokenized = _postJson(
        ctx,
        "/tokenize",
        {
            "content": prompt,
            "add_special": False,
            "parse_special": True,
            "with_pieces": False,
        },
    )
    tokens = tokenized.get("tokens")
    if not isinstance(tokens, list):
        raise RuntimeError("llama.cpp /tokenize response does not contain a tokens list.")
    return len(tokens)


def _measure(ctx, payload):
    if not isinstance(payload, dict):
        raise ValueError("Token budget measurement requires an object payload.")
    text = payload.get("text")
    if type(text) is not str:
        raise TypeError("Token budget measurement requires a string text field.")
    return {"inputTokens": _estimateText(ctx, text)}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.tokenBudget@1", _measure)
