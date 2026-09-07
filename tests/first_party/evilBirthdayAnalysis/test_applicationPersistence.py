# file: tests/first_party/evilBirthdayAnalysis/test_applicationPersistence.py ; version: 1
from __future__ import annotations

import json
from pathlib import Path

from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime.applicationOperations import ApplicationRuntimeOperations
from backend.save import ApplicationStore


_REPO_ROOT = Path(__file__).parents[3]
_SEMANTIC_ADDRESS = "evilanalysis/chat/line/17/semantic"


def _config(tmp_path: Path) -> dict[str, object]:
    chat = tmp_path / "chat.txt"
    chat.write_text(
        "[2024-03-25 19:20:00] #vedal987 viewer: bring gun bring gun bring gun\n",
        encoding="utf-8",
    )
    emotes = tmp_path / "chatEmotes.json"
    emotes.write_text(
        json.dumps({"emotes": {}, "composites": []}),
        encoding="utf-8",
    )
    return {
        "chatFile": str(chat),
        "chatEmotesFile": str(emotes),
    }


def _payload(config: dict[str, object]) -> dict[str, object]:
    source = Path(config["chatFile"])
    return {
        "sourcePath": str(source),
        "sourceObservation": {
            "path": str(source),
            "state": "file",
            "sizeBytes": source.stat().st_size,
            "modifiedTimeNs": source.stat().st_mtime_ns,
            "contentSha256": None,
        },
        "contextStreamStartSeconds": 0.0,
        "contextStreamEndSeconds": 20.0,
        "records": [
            {
                "lineNumber": 17,
                "channel": "#vedal987",
                "message": "viewer: bring gun bring gun bring gun",
                "timestampText": "2024-03-25 19:20:00",
                "rawLine": (
                    "[2024-03-25 19:20:00] #vedal987 "
                    "viewer: bring gun bring gun bring gun"
                ),
                "streamTimeSeconds": 6.0,
                "streamTime": "00:00:06",
                "insideRequestedWindow": True,
            },
        ],
    }


def test_real_evil_chat_semantics_survive_filesystem_application_restart(tmp_path: Path):
    store = ApplicationStore(tmp_path / "saves")
    resolver = PackResolver(roots=(_REPO_ROOT / "first-party",))
    operations = ApplicationRuntimeOperations(
        applicationStore=store,
        packResolver=resolver,
    )
    plan = ManualActivationPlan(
        packIds=(
            "evilBirthdayAnalysis",
            "evilBirthdayAnalysis.chatSemantics",
        ),
    )
    config = _config(tmp_path)
    payload = _payload(config)

    first = operations.createApplication(
        appPackId="evilBirthdayAnalysis",
        plan=plan,
        config=config,
    )
    applicationId = first.applicationId
    firstResult = first.runtime.invokeCapability(
        "evilAnalysis.chatInterpret@1",
        payload,
    )
    assert firstResult["records"][0]["analysis"]["spans"] == [
        {
            "kind": "repeat",
            "count": 3,
            "spans": [{"kind": "text", "text": "bring gun"}],
        },
    ]

    firstRoot = first.runtime.applicationRun.application.committedState
    firstMetadata = firstRoot.metadata(_SEMANTIC_ADDRESS)
    assert firstRoot.revisionId(_SEMANTIC_ADDRESS) == 1
    first.runtime.saveApplication()
    first.close()

    second = operations.loadApplication(
        appPackId="evilBirthdayAnalysis",
        applicationId=applicationId,
        plan=plan,
        config=config,
    )
    secondRoot = second.runtime.applicationRun.application.committedState

    assert secondRoot.revisionId(_SEMANTIC_ADDRESS) == 1
    assert secondRoot.metadata(_SEMANTIC_ADDRESS) == firstMetadata

    secondResult = second.runtime.invokeCapability(
        "evilAnalysis.chatInterpret@1",
        payload,
    )

    assert secondRoot.revisionId(_SEMANTIC_ADDRESS) == 1
    assert secondRoot.metadata(_SEMANTIC_ADDRESS) == firstMetadata
    assert secondResult["records"][0]["analysis"]["spans"] == [
        {
            "kind": "repeat",
            "count": 3,
            "spans": [{"kind": "text", "text": "bring gun"}],
        },
    ]

    second.close()
