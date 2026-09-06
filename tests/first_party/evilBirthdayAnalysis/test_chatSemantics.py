# file: tests/first_party/evilBirthdayAnalysis/test_chatSemantics.py ; version: 6
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.context.codeEntryContext import _MemoryFacade
from backend.save import SaveBundle
from backend.values import CommittedValueLayer


_CODE_ENTRY = (
    Path(__file__).parents[3]
    / "first-party"
    / "applications"
    / "evilBirthdayAnalysis"
    / "packs"
    / "chatSemantics"
    / "codeEntry.py"
)
_SPEC = importlib.util.spec_from_file_location("evilBirthdayChatSemanticsCodeEntry", _CODE_ENTRY)
assert _SPEC is not None and _SPEC.loader is not None
chatSemantics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chatSemantics)


EMOTES = {
    "GIGAEVIL": {"semanticClass": "praise", "classificationSource": "userDefined"},
    "ReallyGunPull": {"semanticClass": "negative", "target": "Vedal", "classificationSource": "userDefined"},
    "Tutel": {"entity": "Vedal", "classificationSource": "userDefined"},
    "Clap": {},
}
COMPOSITES = [
    {
        "tokens": ["ReallyGunPull", "Tutel"],
        "semanticClass": "negative",
        "target": "Vedal",
        "classificationSource": "userDefined",
    }
]


_VOCABULARY_OBSERVATION = {
    "path": "chatEmotes.json",
    "state": "file",
    "sizeBytes": 1,
    "modifiedTimeNs": 1,
    "contentSha256": "vocabulary-hash",
}
_SOURCE_OBSERVATION = {
    "path": "chat.txt",
    "state": "file",
    "sizeBytes": 1,
    "modifiedTimeNs": 1,
    "contentSha256": "source-hash",
}


class _Io:
    def __init__(self, *, emotes=None, vocabularyObservation=None):
        self._emotes = EMOTES if emotes is None else emotes
        self._vocabularyObservation = (
            _VOCABULARY_OBSERVATION
            if vocabularyObservation is None
            else vocabularyObservation
        )

    def readObservedJson(self, path):
        return {
            "value": {"emotes": self._emotes, "composites": COMPOSITES},
            "observation": dict(self._vocabularyObservation),
        }

    def readJson(self, _path):
        return {"emotes": self._emotes, "composites": COMPOSITES}

    def observeFile(self, path, *, contentHash=False):
        del contentHash
        if str(path) == "chatEmotes.json":
            return dict(self._vocabularyObservation)
        return dict(_SOURCE_OBSERVATION)


class _Ctx:
    def __init__(
        self,
        memory=None,
        *,
        emotes=None,
        vocabularyObservation=None,
        implementationId="chat-semantics-impl-1",
    ):
        self.io = _Io(
            emotes=emotes,
            vocabularyObservation=vocabularyObservation,
        )
        self._committedMemory = memory or CommittedValueLayer()
        self.memory = _MemoryFacade(
            state=self._committedMemory,
            requireValid=lambda: None,
            producer={
                "packId": "evilBirthdayAnalysis.chatSemantics",
                "packVersion": "0.1.0",
                "codeEntryId": "chatSemantics",
                "sourceSha256": f"source-{implementationId}",
                "implementationFormat": "python-source@1",
                "implementationId": implementationId,
            },
        )
        self.config = {"chatEmotesFile": "chatEmotes.json"}


def _evaluate(spans):
    return chatSemantics._evaluate(None, {"spans": spans})


def _interpret(ctx, records, *, sourceObservation=None):
    return chatSemantics._interpret(
        ctx,
        {
            "sourcePath": "chat.txt",
            "sourceObservation": dict(sourceObservation or _SOURCE_OBSERVATION),
            "records": records,
        },
    )


def _raw(
    lineNumber: int,
    message: str,
    *,
    streamTimeSeconds: float,
    streamTime: str,
    insideRequestedWindow: bool = True,
) -> dict[str, object]:
    return {
        "lineNumber": lineNumber,
        "channel": "#vedal987",
        "message": message,
        "timestampText": "2024-03-25 19:20:00",
        "rawLine": f"[2024-03-25 19:20:00] #vedal987 {message}",
        "streamTimeSeconds": streamTimeSeconds,
        "streamTime": streamTime,
        "insideRequestedWindow": insideRequestedWindow,
    }


def test_known_user_defined_emote_is_fully_reducible_and_aggregation_eligible():
    result = _evaluate(
        [
            {
                "kind": "emote",
                "name": "GIGAEVIL",
                "count": 3,
                "metadata": {"semanticClass": "praise", "classificationSource": "userDefined"},
            }
        ]
    )

    assert result["lexicallyComplete"] is True
    assert result["semanticallyComplete"] is True
    assert result["aggregationEligible"] is True
    assert result["semanticUnits"] == [
        {
            "meaning": {"semanticClass": "praise", "classificationSource": "userDefined"},
            "count": 3,
        }
    ]


def test_recognized_but_unclassified_emote_is_lexical_not_semantic():
    result = _evaluate([{"kind": "emote", "name": "Clap", "count": 2, "metadata": {}}])

    assert result["lexicallyComplete"] is True
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False
    assert result["semanticUnits"] == []


def test_residual_text_prevents_semantic_flattening():
    result = _evaluate(
        [
            {
                "kind": "emote",
                "name": "GIGAEVIL",
                "count": 1,
                "metadata": {"semanticClass": "praise", "classificationSource": "userDefined"},
            },
            {"kind": "text", "text": "holy shit she actually did it"},
        ]
    )

    assert result["lexicallyComplete"] is False
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False


def test_exact_repeat_multiplies_semantic_occurrence_count():
    result = _evaluate(
        [
            {
                "kind": "repeat",
                "count": 5,
                "spans": [
                    {
                        "kind": "emote",
                        "name": "GIGAEVIL",
                        "count": 1,
                        "metadata": {"semanticClass": "praise", "classificationSource": "userDefined"},
                    }
                ],
            }
        ]
    )

    assert result["structurallyCompressed"] is True
    assert result["semanticUnits"][0]["count"] == 5


def test_non_user_defined_semantic_suggestion_is_not_aggregation_eligible():
    result = _evaluate(
        [
            {
                "kind": "emote",
                "name": "futureEmote",
                "count": 1,
                "metadata": {"semanticClass": "praise", "classificationSource": "llmClusterSuggested"},
            }
        ]
    )

    assert result["lexicallyComplete"] is True
    assert result["semanticallyComplete"] is False
    assert result["aggregationEligible"] is False


def test_invalid_repeat_shape_fails_closed():
    with pytest.raises(ValueError, match="count greater than one"):
        _evaluate([{"kind": "repeat", "count": 1, "spans": [{"kind": "text", "text": "x"}]}])


def test_interpret_dynamically_recognizes_user_shape_and_keeps_raw_message():
    result = _interpret(
        _Ctx(),
        [
                _raw(1, "viewer: GIGAEVIL GIGAEVIL", streamTimeSeconds=5.0, streamTime="00:00:05")
            ],
    )

    record = result["records"][0]
    assert record["message"] == "viewer: GIGAEVIL GIGAEVIL"
    assert record["username"] == "viewer"
    assert record["body"] == "GIGAEVIL GIGAEVIL"
    assert record["analysis"]["kind"] == "userMessage"
    assert record["analysis"]["spans"][0]["kind"] == "emote"
    assert record["analysis"]["spans"][0]["count"] == 2
    assert result["text"] == "00:00:05 viewer: GIGAEVIL x2"


def test_interpret_collapses_exact_repeated_plain_text_sequence():
    result = _interpret(
        _Ctx(),
        [
                _raw(
                    1,
                    "viewer: bring gun bring gun bring gun",
                    streamTimeSeconds=6.0,
                    streamTime="00:00:06",
                )
            ],
    )

    record = result["records"][0]
    assert record["body"] == "bring gun bring gun bring gun"
    assert record["analysis"]["spans"] == [
        {
            "kind": "repeat",
            "count": 3,
            "spans": [{"kind": "text", "text": "bring gun"}],
        }
    ]
    assert result["text"] == "00:00:06 viewer: (bring gun) x3"


def test_interpret_preserves_unknown_message_without_guessing_username_or_body():
    rawMessage = "A moderation or information form not understood by this CodeEntry"
    result = _interpret(
        _Ctx(),
        [_raw(1, rawMessage, streamTimeSeconds=7.0, streamTime="00:00:07")],
    )

    record = result["records"][0]
    assert record["message"] == rawMessage
    assert "username" not in record
    assert "body" not in record
    assert record["analysis"]["kind"] == "unknownMessage"
    assert record["analysis"]["rawMessage"] == rawMessage
    assert record["analysis"]["includedInText"] is True
    assert result["text"] == f"00:00:07 [unclassified] {rawMessage}"


def test_interpret_uses_pre_window_raw_context_to_reconstruct_gift_batch():
    result = _interpret(
        _Ctx(),
        [
                _raw(
                    1,
                    "mybraza: mybraza is gifting 2 Tier 1 Subs to vedal987's community! They've gifted a total of 126 in the channel!",
                    streamTimeSeconds=-1.0,
                    streamTime="-00:00:01",
                    insideRequestedWindow=False,
                ),
                _raw(
                    2,
                    "mybraza: mybraza gifted a Tier 1 sub to Aemable!",
                    streamTimeSeconds=0.0,
                    streamTime="00:00:00",
                ),
                _raw(
                    3,
                    "viewer: HAPPY BIRTHDAY",
                    streamTimeSeconds=1.0,
                    streamTime="00:00:01",
                ),
                _raw(
                    4,
                    "mybraza: mybraza gifted a Tier 1 sub to OtherUser!",
                    streamTimeSeconds=2.0,
                    streamTime="00:00:02",
                ),
            ],
    )

    assert result["records"][0]["analysis"]["includedInText"] is False
    assert result["records"][1]["analysis"]["includedInText"] is False
    assert result["records"][3]["analysis"]["includedInText"] is False
    assert result["records"][1]["analysis"]["partOfGiftBatchLineNumber"] == 1
    assert result["records"][3]["analysis"]["partOfGiftBatchLineNumber"] == 1
    assert result["records"][0]["analysis"]["event"]["recipients"] == ["Aemable", "OtherUser"]
    assert result["text"] == "00:00:01 viewer: HAPPY BIRTHDAY"


def test_interpret_known_fossabot_automation_is_retained_but_suppressed():
    result = _interpret(
        _Ctx(),
        [
                _raw(
                    1,
                    "fossabot: @RatK1ngg_, Your message is too long [warning]",
                    streamTimeSeconds=9.0,
                    streamTime="00:00:09",
                )
            ],
    )

    record = result["records"][0]
    assert record["analysis"]["kind"] == "botEvent"
    assert record["analysis"]["includedInText"] is False
    assert result["text"] == ""


def test_interpret_command_and_confirmed_composite_behavior_remains_explicit():
    result = _interpret(
        _Ctx(),
        [
                _raw(
                    1,
                    "viewer: !clip now",
                    streamTimeSeconds=11.0,
                    streamTime="00:00:11",
                ),
                _raw(
                    2,
                    "viewer: ReallyGunPull Tutel ReallyGunPull Tutel",
                    streamTimeSeconds=12.0,
                    streamTime="00:00:12",
                ),
            ],
    )

    command = result["records"][0]["analysis"]["spans"]
    composite = result["records"][1]["analysis"]["spans"]

    assert command == [{"kind": "command", "command": "clip", "arguments": ["now"]}]
    assert composite == [
        {
            "kind": "composite",
            "tokens": ["ReallyGunPull", "Tutel"],
            "count": 2,
            "metadata": {
                "semanticClass": "negative",
                "target": "Vedal",
                "classificationSource": "userDefined",
            },
        }
    ]
    assert result["text"].splitlines() == [
        "00:00:11 viewer: !clip now",
        "00:00:12 viewer: ReallyGunPull Tutel x2",
    ]


def test_interpret_unknown_fossabot_message_remains_user_message():
    result = _interpret(
        _Ctx(),
        [
                _raw(
                    1,
                    "fossabot: an unfamiliar future message",
                    streamTimeSeconds=10.0,
                    streamTime="00:00:10",
                )
            ],
    )

    assert result["records"][0]["analysis"]["kind"] == "userMessage"
    assert result["text"] == "00:00:10 fossabot: an unfamiliar future message"



def test_line_semantics_reuse_authoritative_cell_when_inputs_are_unchanged():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(
            17,
            "viewer: GIGAEVIL GIGAEVIL",
            streamTimeSeconds=5.0,
            streamTime="00:00:05",
        )
    ]

    first = _interpret(ctx, records)
    assert first["records"][0]["analysis"]["spans"][0]["count"] == 2
    address = chatSemantics._semanticCellAddress(17)
    assert memory.revisionId(address) == 1

    second = _interpret(ctx, records)
    assert second["records"][0]["analysis"]["spans"][0]["count"] == 2
    assert memory.revisionId(address) == 1


def test_line_semantics_replace_only_changed_line_at_same_address():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    firstRecords = [
        _raw(17, "viewer: GIGAEVIL", streamTimeSeconds=5.0, streamTime="00:00:05"),
        _raw(18, "viewer: Clap", streamTimeSeconds=6.0, streamTime="00:00:06"),
    ]
    _interpret(ctx, firstRecords)

    firstAddress = chatSemantics._semanticCellAddress(17)
    secondAddress = chatSemantics._semanticCellAddress(18)
    assert memory.revisionId(firstAddress) == 1
    assert memory.revisionId(secondAddress) == 1

    changedRecords = [
        _raw(17, "viewer: GIGAEVIL", streamTimeSeconds=5.0, streamTime="00:00:05"),
        _raw(18, "viewer: Clap Clap", streamTimeSeconds=6.0, streamTime="00:00:06"),
    ]
    result = _interpret(
        ctx,
        changedRecords,
        sourceObservation={**_SOURCE_OBSERVATION, "contentSha256": "changed-source"},
    )

    assert memory.revisionId(firstAddress) == 1
    assert memory.revisionId(secondAddress) == 2
    assert result["records"][1]["analysis"]["spans"][0]["count"] == 2


def test_line_semantics_survive_save_bundle_rehydration():
    firstMemory = CommittedValueLayer()
    firstCtx = _Ctx(firstMemory)
    records = [
        _raw(
            17,
            "viewer: bring gun bring gun bring gun",
            streamTimeSeconds=6.0,
            streamTime="00:00:06",
        )
    ]
    _interpret(firstCtx, records)

    address = chatSemantics._semanticCellAddress(17)
    assert firstMemory.revisionId(address) == 1

    bundle = SaveBundle.create(
        applicationId="evil-analysis",
        committedState=firstMemory,
    )
    restoredMemory = SaveBundle.fromBytes(bundle.toBytes()).restoreCommittedState()
    restoredCtx = _Ctx(restoredMemory)

    result = _interpret(restoredCtx, records)

    assert restoredMemory.revisionId(address) == 1
    assert result["records"][0]["analysis"]["spans"] == [
        {
            "kind": "repeat",
            "count": 3,
            "spans": [{"kind": "text", "text": "bring gun"}],
        }
    ]



def test_line_semantics_recompute_when_vocabulary_requirement_changes():
    memory = CommittedValueLayer()
    records = [
        _raw(
            20,
            "viewer: NEWEMOTE",
            streamTimeSeconds=8.0,
            streamTime="00:00:08",
        )
    ]

    firstCtx = _Ctx(memory)
    first = _interpret(firstCtx, records)
    address = chatSemantics._semanticCellAddress(20)

    assert first["records"][0]["analysis"]["spans"] == [
        {"kind": "text", "text": "NEWEMOTE"}
    ]
    assert memory.revisionId(address) == 1

    changedObservation = {
        **_VOCABULARY_OBSERVATION,
        "contentSha256": "changed-vocabulary-hash",
    }
    changedEmotes = {
        **EMOTES,
        "NEWEMOTE": {
            "semanticClass": "praise",
            "classificationSource": "userDefined",
        },
    }
    secondCtx = _Ctx(
        memory,
        emotes=changedEmotes,
        vocabularyObservation=changedObservation,
    )
    second = _interpret(secondCtx, records)

    assert second["records"][0]["analysis"]["spans"] == [
        {
            "kind": "emote",
            "name": "NEWEMOTE",
            "count": 1,
            "metadata": {
                "semanticClass": "praise",
                "classificationSource": "userDefined",
            },
        }
    ]
    assert memory.revisionId(address) == 2



def test_line_semantics_ignore_vocabulary_metadata_change_when_content_identity_matches():
    memory = CommittedValueLayer()
    records = [
        _raw(
            21,
            "viewer: GIGAEVIL",
            streamTimeSeconds=9.0,
            streamTime="00:00:09",
        )
    ]

    firstCtx = _Ctx(memory)
    _interpret(firstCtx, records)
    address = chatSemantics._semanticCellAddress(21)
    assert memory.revisionId(address) == 1

    touchedObservation = {
        **_VOCABULARY_OBSERVATION,
        "modifiedTimeNs": 999999,
    }
    secondCtx = _Ctx(
        memory,
        vocabularyObservation=touchedObservation,
    )
    _interpret(secondCtx, records)

    assert memory.revisionId(address) == 1



def test_line_semantics_recompute_when_producer_implementation_changes():
    memory = CommittedValueLayer()
    records = [
        _raw(
            22,
            "viewer: GIGAEVIL",
            streamTimeSeconds=10.0,
            streamTime="00:00:10",
        )
    ]

    firstCtx = _Ctx(memory, implementationId="implementation-a")
    _interpret(firstCtx, records)
    address = chatSemantics._semanticCellAddress(22)
    assert memory.revisionId(address) == 1
    firstMetadata = memory.metadata(address)
    assert firstMetadata["producer"]["implementationId"] == "implementation-a"

    secondCtx = _Ctx(memory, implementationId="implementation-b")
    _interpret(secondCtx, records)

    assert memory.revisionId(address) == 2
    secondMetadata = memory.metadata(address)
    assert secondMetadata["producer"]["implementationId"] == "implementation-b"



def test_second_bucket_reuses_when_member_semantics_and_timing_are_unchanged():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(30, "viewer: GIGAEVIL", streamTimeSeconds=12.0, streamTime="00:00:12"),
        _raw(31, "other: Clap", streamTimeSeconds=12.0, streamTime="00:00:12"),
    ]

    first = _interpret(ctx, records)
    bucketAddress = chatSemantics._secondCellAddress(12)

    assert len(first["secondBuckets"]) == 1
    assert first["secondBuckets"][0]["address"] == bucketAddress
    assert [member["lineNumber"] for member in first["secondBuckets"][0]["value"]["members"]] == [30, 31]
    assert memory.revisionId(bucketAddress) == 1

    second = _interpret(ctx, records)

    assert len(second["secondBuckets"]) == 1
    assert memory.revisionId(bucketAddress) == 1
    assert second["secondBuckets"][0]["dependency"] == first["secondBuckets"][0]["dependency"]


def test_second_bucket_recomputes_when_one_member_semantic_changes():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    firstRecords = [
        _raw(32, "viewer: GIGAEVIL", streamTimeSeconds=13.0, streamTime="00:00:13"),
        _raw(33, "other: Clap", streamTimeSeconds=13.0, streamTime="00:00:13"),
    ]
    _interpret(ctx, firstRecords)

    firstLineAddress = chatSemantics._semanticCellAddress(32)
    secondLineAddress = chatSemantics._semanticCellAddress(33)
    bucketAddress = chatSemantics._secondCellAddress(13)
    assert memory.revisionId(firstLineAddress) == 1
    assert memory.revisionId(secondLineAddress) == 1
    assert memory.revisionId(bucketAddress) == 1

    changedRecords = [
        _raw(32, "viewer: GIGAEVIL", streamTimeSeconds=13.0, streamTime="00:00:13"),
        _raw(33, "other: Clap Clap", streamTimeSeconds=13.0, streamTime="00:00:13"),
    ]
    result = _interpret(
        ctx,
        changedRecords,
        sourceObservation={**_SOURCE_OBSERVATION, "contentSha256": "changed-source"},
    )

    assert memory.revisionId(firstLineAddress) == 1
    assert memory.revisionId(secondLineAddress) == 2
    assert memory.revisionId(bucketAddress) == 2
    assert result["secondBuckets"][0]["value"]["members"][1]["semantic"]["address"] == secondLineAddress


def test_second_bucket_dependency_survives_save_bundle_rehydration():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(34, "viewer: bring gun bring gun", streamTimeSeconds=14.0, streamTime="00:00:14"),
        _raw(35, "other: GIGAEVIL", streamTimeSeconds=14.0, streamTime="00:00:14"),
    ]
    first = _interpret(ctx, records)
    bucketAddress = chatSemantics._secondCellAddress(14)
    firstDependency = first["secondBuckets"][0]["dependency"]

    bundle = SaveBundle.create(applicationId="evil-analysis", committedState=memory)
    restoredMemory = SaveBundle.fromBytes(bundle.toBytes()).restoreCommittedState()
    restoredCtx = _Ctx(restoredMemory)

    second = _interpret(restoredCtx, records)

    assert restoredMemory.revisionId(bucketAddress) == 1
    assert second["secondBuckets"][0]["dependency"] == firstDependency


def test_second_bucket_address_supports_negative_lookback_seconds():
    assert chatSemantics._secondCellAddress(-1) == "evilanalysis/chat/second/n1/semantic"
    assert chatSemantics._secondCellAddress(0) == "evilanalysis/chat/second/s0/semantic"
