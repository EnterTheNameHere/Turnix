from enum import Enum

class LLMPipelineStages(str, Enum):
    ValidateInput = "ValidateInput"
    SanitizeInput = "SanitizeInput"
    GenerateQueryItems = "GenerateQueryItems"
    FilterQueryItems = "FilterQueryItems"
    FinalizePrompt = "FinalizePrompt"
    ValidateStreamResponse = "ValidateStreamResponse"
    SanitizeStreamResponse = "SanitizeStreamResponse"
    ProcessStreamResponse = "ProcessStreamResponse"
    ReceivedResponse = "ReceivedResponse"
