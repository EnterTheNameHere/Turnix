from abc import ABC, abstractmethod
from typing import AsyncGenerator
from core.schema import QueryItem
from core.drivers.base_driver import BaseDriver

class LLMClient(BaseDriver, ABC):
    @abstractmethod
    async def generate(self, queryItems: list[QueryItem]) -> dict:
        """ Generate a response based on the given query items. """
        pass

    @abstractmethod
    async def streamGenerate(self, queryItems: list[QueryItem]) -> AsyncGenerator[dict, None]:
        """ Generate a response, streamed back, based on the given query items. """
        pass

    @abstractmethod
    async def tokenize(self, text: str) -> list[int]:
        """ Return token list for given text. """
        pass

    async def countTokens(self, text: str) -> int:
        """ Return token count for given text. """
        return len(await self.tokenize(text))
