from enum import Enum

class PipelineStage(str, Enum):
    PreInput = "PreInput"
    PostInput = "PostInput"
    PreQueryBuild = "PreQueryBuild"
    PostQueryBuild = "PostQueryBuild"
    PreQuerySend = "PreQuerySend"
    PostQueryReply = "PostQueryReply"
    PreStoreHistory = "PreStoreHistory"
    PostStoreHistory = "PostStoreHistory"
