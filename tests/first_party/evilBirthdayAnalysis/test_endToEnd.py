# file: tests/first_party/evilBirthdayAnalysis/test_endToEnd.py ; version: 2
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.orchestration import JobState
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime.runtimeHost import RuntimeHost
from backend.save import ApplicationStore
from backend.values import MISSING


_REPO_ROOT = Path(__file__).parents[3]


def _writeFakeLlmPack(root: Path) -> None:
    pack = root / "fakeLlm"
    pack.mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "packId": "test.fakeLlm",
                "kind": "modPack",
                "version": "0.0.0",
                "codeEntries": [
                    {
                        "id": "provider",
                        "source": "codeEntry.py",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (pack / "codeEntry.py").write_text(
        """
from backend.llm.llmTypes import LlmExecutionProfile, LlmStreamEvent


class _Estimator:
    def estimateInputTokens(self, query):
        if query.formatId != "text/plain" or type(query.payload) is not str:
            raise ValueError("fake provider accepts text/plain strings only")
        return max(1, len(query.payload.split()))


class _Provider:
    def getExecutionProfile(self, *, model, providerOptions):
        return LlmExecutionProfile(
            contextWindowTokens=4096,
            tokenEstimator=_Estimator(),
            metadata={
                "provider": "deterministic-fake",
                "model": model,
            },
        )

    def stream(self, request):
        yield LlmStreamEvent(eventType="delta", text="FAKE ")
        yield LlmStreamEvent(eventType="delta", text="RESPONSE")
        yield LlmStreamEvent(
            eventType="completed",
            metadata={"finishReason": "stop"},
        )


class _PreviewOnlyProvider(_Provider):
    def stream(self, request):
        raise AssertionError("prompt preview must not invoke provider stream")
        yield  # pragma: no cover


def onLoad(ctx):
    ctx.llm.registerProvider("test.fake", _Provider())
    ctx.llm.registerProvider("test.preview", _PreviewOnlyProvider())
""".lstrip(),
        encoding="utf-8",
    )


def _writeFixtureFiles(tmp_path: Path) -> dict[str, object]:
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "words": [
                            {"word": "I", "start": 1.0, "end": 1.2},
                            {"word": "am", "start": 1.3, "end": 1.5},
                            {"word": "Evil", "start": 1.6, "end": 2.0},
                            {"word": "today", "start": 2.1, "end": 2.5},
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    chat = tmp_path / "chat.txt"
    chat.write_text(
        "[2024-03-25 19:00:02] #vedal987 viewer_one: GIGAEVIL\n"
        "[2024-03-25 19:00:03] #vedal987 viewer_two: this is wild\n"
        "[2024-03-25 19:00:04] #vedal987 vedal987: behave\n",
        encoding="utf-8",
    )

    emotes = tmp_path / "chatEmotes.json"
    emotes.write_text(
        json.dumps(
            {
                "emotes": {
                    "GIGAEVIL": {
                        "meaning": {
                            "semanticClass": "praise",
                            "classificationSource": "userDefined",
                        }
                    }
                },
                "composites": [],
            }
        ),
        encoding="utf-8",
    )

    prompts = tmp_path / "prompts.json"
    prompts.write_text(
        json.dumps(
            {
                "characterization": {
                    "description": "Characterization test prompt.",
                    "prompt": (
                        "Analyze Evil's characterization in this evidence. "
                        "Ground every claim in the supplied transcript and chat."
                    ),
                }
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "results"
    return {
        "activeProfile": "test-profile",
        "profiles": {
            "test-profile": {
                "description": "One deterministic ten-second context window.",
                "includeChat": True,
                "chatLayout": "interleaved",
                "chunkSeconds": 10,
                "contextOffsetsSeconds": [0],
            }
        },
        "analysisBatch": {
            "start": "00:00:00",
            "end": "00:00:10",
            "stepSeconds": 10,
        },
        "activePrompt": "characterization",
        "promptsFile": str(prompts),
        "transcriptFile": str(transcript),
        "chatFile": str(chat),
        "chatEmotesFile": str(emotes),
        "chatStartTime": "19:00:00",
        "streamStartTime": "00:00:00",
        "definedUsers": {
            "vedal987": {"identity": "Vedal"},
        },
        "chatBudget": {
            "optionalContextMaxFraction": 0.60,
        },
        "outputDirectory": str(output),
        "llm": {
            "provider": "test.fake",
            "model": "fake-model",
            "providerOptions": {},
        },
    }


def _plan() -> ManualActivationPlan:
    return ManualActivationPlan(
        packIds=(
            "evilBirthdayAnalysis",
            "test.fakeLlm",
            "evilBirthdayAnalysis.prompts",
            "evilBirthdayAnalysis.transcript",
            "evilBirthdayAnalysis.chat",
            "evilBirthdayAnalysis.chatSemantics",
            "evilBirthdayAnalysis.identity",
            "evilBirthdayAnalysis.tokenBudget",
            "evilBirthdayAnalysis.results",
            "evilBirthdayAnalysis.analysis",
        )
    )


def _queryItemAddress(itemId: str) -> str:
    digest = hashlib.sha256(itemId.encode("utf-8")).hexdigest()
    return f"processing/evilbirthday/items/{digest}"


def test_real_evil_analysis_runs_end_to_end_and_reuses_persistent_material_after_restart(
    tmp_path: Path,
):
    fakeRoot = tmp_path / "packs"
    _writeFakeLlmPack(fakeRoot)
    config = _writeFixtureFiles(tmp_path)
    store = ApplicationStore(tmp_path / "saves")
    resolver = PackResolver(
        roots=(
            fakeRoot,
            _REPO_ROOT / "first-party",
        )
    )
    host = RuntimeHost(
        applicationStore=store,
        packResolver=resolver,
    )
    host.start()

    first = host.createApplication(
        appPackId="evilBirthdayAnalysis",
        plan=_plan(),
        config=config,
    )
    applicationId = first.applicationRun.application.applicationId

    firstJob = first.runJob("evilAnalysis.run@1")
    assert firstJob.state is JobState.SUCCEEDED
    assert firstJob.authoritativeStateAccepted is True
    assert isinstance(firstJob.result, dict)

    firstResults = firstJob.result["results"]
    assert len(firstResults) == 1
    firstWindow = firstResults[0]
    firstProcessingRunId = firstWindow["processingRunId"]
    firstResult = firstWindow["result"]

    root = first.applicationRun.application.committedState
    firstRunEvidence = root.load(
        f"processing/evilbirthday/runs/{firstProcessingRunId}"
    )
    assert firstRunEvidence["response"]["rawText"] == "FAKE RESPONSE"
    assert "I am Evil today" in firstRunEvidence["query"]["payload"]
    assert "this is wild" in firstRunEvidence["query"]["payload"]

    semanticAddress = "evilanalysis/chat/line/1/semantic"
    semanticRevision = root.revisionId(semanticAddress)
    semanticMetadata = root.metadata(semanticAddress)
    assert semanticRevision == 1

    chatItem = root.load(_queryItemAddress("chat:1"))
    assert chatItem["kind"] == "chat"
    assert chatItem["content"] == "GIGAEVIL"

    resultAddress = f"evilanalysis/results/{firstProcessingRunId}"
    assert root.load(resultAddress) == firstResult

    firstExport = Path(config["outputDirectory"]) / f"{firstResult['resultId']}.json"
    assert firstExport.is_file()
    exported = json.loads(firstExport.read_text(encoding="utf-8"))
    assert exported["response"]["rawText"] == "FAKE RESPONSE"
    assert exported["input"]["exactPayload"] == firstRunEvidence["query"]["payload"]

    firstBundle = first.saveApplication()
    assert firstBundle.generation >= 2
    firstRunId = first.applicationRun.applicationRunId
    host.closeApplicationRun(firstRunId)

    loaded = host.loadApplication(
        appPackId="evilBirthdayAnalysis",
        applicationId=applicationId,
        plan=_plan(),
        config=config,
    )
    assert loaded.applicationRun.applicationRunId != firstRunId
    loadedRoot = loaded.applicationRun.application.committedState

    assert loadedRoot.revisionId(semanticAddress) == semanticRevision
    assert loadedRoot.metadata(semanticAddress) == semanticMetadata
    assert loadedRoot.load(
        f"processing/evilbirthday/runs/{firstProcessingRunId}"
    ) == firstRunEvidence

    secondJob = loaded.runJob("evilAnalysis.run@1")
    assert secondJob.state is JobState.SUCCEEDED
    assert secondJob.authoritativeStateAccepted is True
    secondWindow = secondJob.result["results"][0]
    secondProcessingRunId = secondWindow["processingRunId"]

    assert secondProcessingRunId != firstProcessingRunId
    assert loadedRoot.revisionId(semanticAddress) == semanticRevision
    assert loadedRoot.metadata(semanticAddress) == semanticMetadata
    assert loadedRoot.load(
        f"processing/evilbirthday/runs/{firstProcessingRunId}"
    ) == firstRunEvidence
    assert loadedRoot.load(
        f"processing/evilbirthday/runs/{secondProcessingRunId}"
    )["response"]["rawText"] == "FAKE RESPONSE"

    currentChatItem = loadedRoot.load(_queryItemAddress("chat:1"))
    assert currentChatItem == chatItem

    secondResult = secondWindow["result"]
    secondExport = Path(config["outputDirectory"]) / f"{secondResult['resultId']}.json"
    assert secondExport.is_file()
    assert secondExport != firstExport

    loaded.saveApplication()
    host.stop()

    restored = store.load(
        appPackId="evilBirthdayAnalysis",
        applicationId=applicationId,
    ).bundle.restoreCommittedState()

    assert restored.revisionId(semanticAddress) == semanticRevision
    assert restored.load(
        f"processing/evilbirthday/runs/{firstProcessingRunId}"
    ) == firstRunEvidence
    assert restored.load(
        f"processing/evilbirthday/runs/{secondProcessingRunId}"
    )["response"]["rawText"] == "FAKE RESPONSE"


def test_real_evil_prompt_preview_builds_exact_query_without_engine_call(tmp_path: Path):
    fakeRoot = tmp_path / "packs"
    _writeFakeLlmPack(fakeRoot)
    config = _writeFixtureFiles(tmp_path)
    config["llm"] = {
        "provider": "test.preview",
        "model": "fake-model",
        "providerOptions": {},
    }
    store = ApplicationStore(tmp_path / "saves")
    resolver = PackResolver(
        roots=(
            fakeRoot,
            _REPO_ROOT / "first-party",
        )
    )
    host = RuntimeHost(
        applicationStore=store,
        packResolver=resolver,
    )
    host.start()
    runtime = host.createApplication(
        appPackId="evilBirthdayAnalysis",
        plan=_plan(),
        config=config,
    )

    try:
        job = runtime.runJob(
            "evilAnalysis.previewPrompt@1",
            {"position": "00:00:00"},
        )

        assert job.state is JobState.SUCCEEDED
        assert job.authoritativeStateAccepted is True
        preview = job.result
        assert isinstance(preview, dict)
        assert preview["provider"] == "test.preview"
        assert preview["model"] == "fake-model"
        assert type(preview["inputTokens"]) is int
        assert preview["inputTokens"] > 0
        assert preview["executionProfile"]["contextWindowTokens"] == 4096

        query = preview["query"]
        assert query["formatId"] == "text/plain"
        assert "I am Evil today" in query["payload"]
        assert "this is wild" in query["payload"]
        assert "Analyze Evil's characterization" in query["payload"]

        root = runtime.applicationRun.application.committedState
        assert root.revisionId("evilanalysis/chat/line/1/semantic") == 1
        assert root.load("processing/evilbirthday/lastrun") is MISSING

        snapshot = root.snapshot()
        assert not any(
            address.startswith("processing/evilbirthday/runs/")
            for address in snapshot
        )
        assert not any(
            address.startswith("evilanalysis/results/")
            for address in snapshot
        )
        output = Path(config["outputDirectory"])
        assert output.exists() is False or list(output.iterdir()) == []
    finally:
        host.stop()
