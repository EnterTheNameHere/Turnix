from pydantic import BaseModel, Field, ConfigDict, computed_field
from pydantic.alias_generators import to_camel
from typing import Optional, Any
from core.stringjson import safe_json_dumps


# === Core Pipeline State (internal only) ===

class LLMPipelineState(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    sessionId: str
    rawUserMessage: str
    sanitizedUserMessage: str = ""
    assistantMessage: Optional[str] = ""
    queryItems: list["QueryItem"] = Field(default_factory=list)
    rawModelResponse: dict = Field(default_factory=dict)
    sanitizedModelResponse: dict = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunk: str = ""
    isFinal: bool = False

    @computed_field
    @property
    def userMessage(self) -> str:
        return self.sanitizedUserMessage

# === Query Item ===


class QueryItem(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: Optional[str]
    type: str # "systemPrompt", "gmPrompt", "message", "memory", "worldInfo"
    role: Optional[str] = None
    rawText: str = Field(alias="content")
    priority: int = 0
    hardInclude: bool = False # Bypass type's token count limit
    metadata: dict[str, Any] = Field(default_factory=dict)
    preformatted: dict[str, str] = Field(default_factory=dict)
    sanitizedResponse: dict = Field(default_factory=dict)

    def tokenCount(self, tokenizer=None, ignoreCache: bool = False) -> int:
        cacheKey = "cachedTokenCount"
        if not ignoreCache and cacheKey in self.metadata:
            return self.metadata[cacheKey]

        # TODO: Implement calling LLM tokenizer

        count = 0
        self.metadata[cacheKey] = count
        return count

    def text(self, format: str = "default", ignoreCache: bool = False) -> str:
        if not ignoreCache and format in self.preformatted:
            return self.preformatted[format]
        rendered = self._render(format)
        self.preformatted[format] = rendered
        return rendered
    
    def _render(self, format: str) -> str:
        """ Render rawText using specified format. """

        if format == "default":
            if self.type == "message":
                role = self.role or "user"
                return f"{role}: {self.rawText}"
            else:
                return self.rawText
        
        elif format == "chatml":
            # ChatML format (e.g. <|im_start|>user\ntext<|im_end|>)
            if self.type == "message":
                role = self.role or "user"
                return f"<|im_start|>{role}\n{self.rawText}<|im_end>"
            else:
                return f"{self.rawText}"
        
        elif format == "json":
            if self.type == "message":
                return safe_json_dumps({
                    "type": self.type,
                    "role": self.role or "user",
                    "text": self.rawText,                     
                })
            else:
                return safe_json_dumps({
                    "type": self.type,
                    "text": self.rawText
                })
        
        elif format == "markdown":
            if self.type == "message":
                role = self.role or "user"
                return f"**{self.type.upper()}**:\n\n{role}:\n{self.rawText}"

            return f"**{self.type.upper()}**\n\n{self.rawText}"

        else:
            # Fallback to no formatting
            return self.rawText


# === Pipeline Stage Schemas ===

class BaseStageData(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    sessionId: str
    rawUserMessage: str
    sanitizedUserMessage: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __setattr__(self, name, value):
        if name == "sessionId" and hasattr(self, "sessionId"):
            raise AttributeError("'sessionId' is read-only after initiation.")
        super().__setattr__(name, value)

    @computed_field
    @property
    def userMessage(self) -> str:
        return self.sanitizedUserMessage

class QueryItemsStageData(BaseStageData):
    queryItems: list[QueryItem] = Field(default_factory=list)

class StreamStageData(BaseStageData):
    chunk: str
    isFinal: bool
    rawModelResponse: dict = Field(default_factory=dict)
    sanitizedModelResponse: dict = Field(default_factory=dict)

    @computed_field
    @property
    def soFar(self) -> dict[str, Any]:
        return self.sanitizedModelResponse

class ValidateInputData(BaseStageData):
    pass

class SanitizeInputData(BaseStageData):
    pass

class GenerateQueryItemsData(QueryItemsStageData):
    pass

class FilterQueryItemsData(QueryItemsStageData):
    pass

class FinalizePromptData(QueryItemsStageData):
    pass

class ValidateStreamResponseData(StreamStageData):
    pass

class SanitizeStreamResponseData(StreamStageData):
    pass

class ProcessStreamResponseData(StreamStageData):
    pass

class ReceivedResponseData(BaseStageData):
    assistantMessage: Optional[str] = ""
    rawModelResponse: dict = Field(default_factory=dict)
    sanitizedModelResponse: dict = Field(default_factory=dict)
    


# TODO: make sure pipeline stages are matching registry
schemaRegistry = {
    "ValidateInput": ValidateInputData,
    "SanitizeInput": SanitizeInputData,
    "GenerateQueryItems": GenerateQueryItemsData,
    "FilterQueryItems": FilterQueryItemsData,
    "FinalizePrompt": FinalizePromptData,
    "ValidateStreamResponse": ValidateStreamResponseData,
    "SanitizeStreamResponse": SanitizeStreamResponseData,
    "ProcessStreamResponse": ProcessStreamResponseData,
    "ReceivedResponse": ReceivedResponseData,
}
