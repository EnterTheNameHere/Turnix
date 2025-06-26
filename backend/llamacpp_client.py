import httpx
import requests
from typing import AsyncGenerator
from core.drivers.llm_client import LLMClient
from core.schema import QueryItem
from core.drivers.driver_registry import driverRegistry

import logging
logger = logging.getLogger(__name__)

TOKEN_COUNT_ENDPOINT = "http://localhost:1234/tokenize"
COMPLETIONS_ENDPOINT = "http://localhost:1234/v1/chat/completions"

class LlamaCppClient(LLMClient):
    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    async def call(self, inputData: dict) -> dict:
        try:
            payload = {
                "messages": inputData.get("messages", []),
                "temperature": inputData.get("temperature", 0.7),
                "top_p": inputData.get("top_p", 0.95),
                "stream": False,
                "max_tokens": inputData.get("max_tokens", 2048),
                "stop": inputData.get("stop",[ "</response>" ]),
            }
            #logger.info(f"Payload sent to LLM: {payload}")
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(COMPLETIONS_ENDPOINT, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError as e:
            print(f"Connection error calling model API: {e}")
            return { "failed": True, "reason": e }
        except httpx.RequestError as e:
            print(f"Request error calling model API: {e}")
            return { "failed": True, "reason": e }
        except httpx.HTTPStatusError as e:
            print(f"Error calling model API: {e}")
            return { "failed": True, "reason": e }

    async def generate(self, queryItems: list[QueryItem]) -> dict:
        messages = [item.model_dump(by_alias=True) for item in queryItems]
        return await self.call({"messages": messages})

    async def streamGenerate(self, queryItems: list[QueryItem]):
        payload = {
            "messages": [item.model_dump(by_alias=True) for item in queryItems],
            "temperature": 0.7,
            "top_p": 0.95,
            "stream": True,
            "max_tokens": 2048,
            "stop": [ "</response>" ],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", COMPLETIONS_ENDPOINT, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    logger.debug(f"Received line from model API: {line}")
                    if line.startswith("data: "):
                        line = line[6:]
                        if line == "[DONE]":
                            break
                        yield line

    async def tokenize(self, text: str) -> list[int]:
        try:
            payload = {
                "content": text,
                "special": True,
            }
            response = requests.post(TOKEN_COUNT_ENDPOINT, json=payload)
            response.raise_for_status()
            tokens = response.json().get("tokens", [])
            return tokens
        except requests.exceptions.RequestException as e:
            logger.warning(f"LLM tokenizing failed: {e}")
            raise

    def describe(self) -> dict:
        return {
            "type": "llm",
            "provider": "llama.cpp",
            "endpoint": COMPLETIONS_ENDPOINT,
            "tokenEndpoint": TOKEN_COUNT_ENDPOINT
        }

driverRegistry.registerDriver("llm", "llama.cpp", LlamaCppClient)
