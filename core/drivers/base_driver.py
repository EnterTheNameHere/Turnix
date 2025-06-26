from abc import ABC, abstractmethod

class BaseDriver(ABC):
    @abstractmethod
    async def call(self, inputData: dict) -> dict:
        """
        Perform an inference call with the given input data.
        """
        pass

    @abstractmethod
    def describe(self) -> dict:
        """
        Return metadata describing the driver (type, endpoint, model, etc.)
        """
