from typing import Type, cast
from pydantic import BaseModel
from core.pipeline_stages import LLMPipelineStages
from core.schema import LLMPipelineState, schemaRegistry, QueryItem
from core.observer_bus import ObserverBus

# TODO: make it driver loader instead of hardcoding
from backend.llamacpp_client import LlamaCppClient #<< Don't delete
from core.drivers.driver_registry import driverRegistry
from core.drivers.llmclient import LLMClient

import logging
logger = logging.getLogger(__name__)

def buildStageData(stageType: Type[BaseModel], state: LLMPipelineState) -> BaseModel:
    """
    Constructs a pipeline stage data model by selecting only the fields
    required by the schema from the global PipelineState.

    This prevents manual copying of field like sessionId, userMessage, etc.,
    and ensures all expected data for the stage is present without redundancy.
    """

    # Filter only the fields that the target schema class expects
    stageFieldNames = stageType.model_fields.keys()
    relevantData = {name: getattr(state, name) for name in stageFieldNames if hasattr(state, name)}
    return stageType(**relevantData)


class LLMPipeline:
    def __init__(self, observerBus: ObserverBus):
        self.observerBus = observerBus
        self.modelClient = cast(LLMClient, driverRegistry.getDriver("llm", "llama.cpp")())

    async def process(self, sessionId: str, userMessage: str) -> dict:
        # TODO: Pydantic can throw validation error, so we should handle it
        # Initialize full pipeline state
        state = LLMPipelineState(sessionId=sessionId, rawUserMessage=userMessage)

        stagesInOrder = [
            LLMPipelineStages.ValidateInput,
            LLMPipelineStages.SanitizeInput,
            LLMPipelineStages.GenerateQueryItems,
            LLMPipelineStages.FilterQueryItems,
            LLMPipelineStages.FinalizePrompt,
            "__MODEL_CALL__",
            LLMPipelineStages.ValidateStreamResponse,
            LLMPipelineStages.SanitizeStreamResponse,
            LLMPipelineStages.ProcessStreamResponse,
            LLMPipelineStages.ReceivedResponse,
        ]

        try:
            for stage in stagesInOrder:
                if stage == "__MODEL_CALL__":
                    logger.info(f"Calling LLM with final queryItems...")

                    modelResponse = await self.modelClient.generate(state.queryItems)
                    logger.info(f"Model response: {modelResponse}")
                    state.rawModelResponse = modelResponse

                    # Try to extract the assistant's reply if available
                    try:
                        replyText = modelResponse["choices"][0]["message"]["content"]
                    except Exception:
                        logger.exception(f"Model response parsing failed.")
                        replyText = "[Error: Invalid model response]"
                    
                    state.assistantMessage = replyText
                    continue
                
                # Build input for this stage from current state
                schemaClass = schemaRegistry[stage]
                stageInput = buildStageData(schemaClass, state)

                logger.info(f"Running {stage}")
                
                # === TESTING ===

                # Copy rawUserMessage to sanitizedUserMessage
                if stage == LLMPipelineStages.SanitizeInput:
                    stageInput.sanitizedUserMessage = stageInput.rawUserMessage # pyright: ignore[reportAttributeAccessIssue]

                # Create QueryItem from userMessage
                if stage == LLMPipelineStages.GenerateQueryItems:
                    new_query_item = QueryItem(type="message", content=state.userMessage, role="user", id="42")
                    stageInput.queryItems.append(new_query_item) # pyright: ignore[reportAttributeAccessIssue]

                # === END TESTING ===

                stageOutput = await self.observerBus.run(stage, stageInput)

                # Merge updated fields back into PipelineState
                for field in stageOutput.model_fields:
                    setattr(state, field, getattr(stageOutput, field))
        except Exception:
            logger.exception(f"Error running '{sessionId}' session's pipeline.")
            raise

        print("PRE RETURN PIPELINE RUN")
        return { "reply": state.assistantMessage }
