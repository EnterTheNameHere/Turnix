from enum import Enum

class PipelineStage(str, Enum):
    SanitizeAndValidateInput = "SanitizeAndValidateInput"
    InputAccepted = "InputAccepted"
    GenerateQueryItems = "GenerateQueryItems"
    FinalizePrompt = "FinalizePrompt"
    SanitizeAndValidateResponse = "SanitizeAndValidateResponse"
    ProcessResponseAndUpdateState = "ProcessResponseAndUpdateState"
    UpdateUI = "UpdateUI"
