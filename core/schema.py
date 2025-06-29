from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Any
from core.stringjson import safe_json_dumps
from core.utils.naming import toCamel



# === Core Pipeline State (internal only) ===


class PipelineState(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

    sessionId: str
    userMessage: str
    assistantMessage: Optional[str] = ""
    queryItems: list["QueryItem"] = Field(default_factory=list)
    rawModelResponse: dict = Field(default_factory=dict)
    sanitizedModelResponse: dict = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


# === Query Item ===


class QueryItem(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

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

        if tokenizer is None:
            from backend.token_counter import TokenCounter
            tokenizer = TokenCounter

        count = tokenizer.count(self.text(ignoreCache=True))
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


class SanitizeAndValidateInputData(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

    sessionId: str
    userMessage: str
    metadata: dict[str, Any] = Field(default_factory=dict)

class InputAcceptedData(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

    sessionId: str
    userMessage: str
    queryItems: list[QueryItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class GenerateQueryItemsData(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

    sessionId: str
    queryItems: list[QueryItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class FinalizePromptData(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

    sessionId: str
    queryItems: list[QueryItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class SanitizeAndValidateResponseData(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

    sessionId: str
    rawModelResponse: dict = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)



class ProcessResponseAndUpdateStateData(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

    sessionId: str
    userMessage: str
    assistantMessage: Optional[str] = ""
    rawModelResponse: dict = Field(default_factory=dict)
    sanitizedModelResponse: dict = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

class UpdateUIData(BaseModel):
    model_config = ConfigDict(alias_generator=toCamel, populate_by_name=True)

    sessionId: str
    userMessage: str
    assistantMessage: Optional[str] = ""
    rawModelResponse: dict = Field(default_factory=dict)
    sanitizedModelResponse: dict = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    

# TODO: make sure pipeline stages are matching registry
schemaRegistry = {
    "SanitizeAndValidateInput": SanitizeAndValidateInputData,
    "InputAccepted": InputAcceptedData,
    "GenerateQueryItems": GenerateQueryItemsData,
    "FinalizePrompt": FinalizePromptData,
    "SanitizeAndValidateResponse": SanitizeAndValidateResponseData,
    "ProcessResponseAndUpdateState": ProcessResponseAndUpdateStateData,
    "UpdateUI": UpdateUIData,
}
