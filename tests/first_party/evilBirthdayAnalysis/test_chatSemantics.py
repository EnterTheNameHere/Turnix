# file: tests/first_party/evilBirthdayAnalysis/test_chatSemantics.py ; version: 11
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


def _interpret(
    ctx,
    records,
    *,
    sourceObservation=None,
    contextStartSeconds=None,
    contextEndSeconds=None,
):
    times = [float(record["streamTimeSeconds"]) for record in records]
    inferredStart = (min(times) - 2.0) if times else -2.0
    inferredEnd = (max(times) + 3.0) if times else 2.0
    return chatSemantics._interpret(
        ctx,
        {
            "sourcePath": "chat.txt",
            "sourceObservation": dict(sourceObservation or _SOURCE_OBSERVATION),
            "contextStreamStartSeconds": (
                inferredStart if contextStartSeconds is None else contextStartSeconds
            ),
            "contextStreamEndSeconds": (
                inferredEnd if contextEndSeconds is None else contextEndSeconds
            ),
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



def test_second_aggregate_persists_identical_canonical_user_message_group():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(50, "alice: GIGAEVIL GIGAEVIL", streamTimeSeconds=20.0, streamTime="00:00:20"),
        _raw(51, "bob: GIGAEVIL GIGAEVIL", streamTimeSeconds=20.0, streamTime="00:00:20"),
        _raw(52, "charlie: Clap", streamTimeSeconds=20.0, streamTime="00:00:20"),
    ]

    result = _interpret(ctx, records)
    aggregate = result["secondAggregates"][0]
    aggregateAddress = chatSemantics._secondAggregateAddress(20)

    assert aggregate["address"] == aggregateAddress
    assert memory.revisionId(aggregateAddress) == 1
    entries = aggregate["value"]["entries"]
    assert entries[0]["kind"] == "identicalCanonicalMessage"
    assert entries[0]["lineNumbers"] == [50, 51]
    assert entries[0]["sourceUsernames"] == ["alice", "bob"]
    assert entries[0]["messageCount"] == 2
    assert entries[0]["uniqueSourceUserCount"] == 2
    assert entries[1]["kind"] == "message"
    assert entries[1]["lineNumber"] == 52


def test_second_aggregate_reuses_when_second_bucket_dependency_is_unchanged():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(53, "alice: GIGAEVIL", streamTimeSeconds=21.0, streamTime="00:00:21"),
        _raw(54, "bob: GIGAEVIL", streamTimeSeconds=21.0, streamTime="00:00:21"),
    ]

    first = _interpret(ctx, records)
    aggregateAddress = chatSemantics._secondAggregateAddress(21)
    assert memory.revisionId(aggregateAddress) == 1

    second = _interpret(ctx, records)

    assert memory.revisionId(aggregateAddress) == 1
    assert second["secondAggregates"][0]["dependency"] == first["secondAggregates"][0]["dependency"]


def test_second_aggregate_recomputes_when_second_bucket_changes():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    firstRecords = [
        _raw(55, "alice: GIGAEVIL", streamTimeSeconds=22.0, streamTime="00:00:22"),
        _raw(56, "bob: GIGAEVIL", streamTimeSeconds=22.0, streamTime="00:00:22"),
    ]
    _interpret(ctx, firstRecords)

    bucketAddress = chatSemantics._secondCellAddress(22)
    aggregateAddress = chatSemantics._secondAggregateAddress(22)
    assert memory.revisionId(bucketAddress) == 1
    assert memory.revisionId(aggregateAddress) == 1

    changed = [
        _raw(55, "alice: GIGAEVIL", streamTimeSeconds=22.0, streamTime="00:00:22"),
        _raw(56, "bob: Clap", streamTimeSeconds=22.0, streamTime="00:00:22"),
    ]
    result = _interpret(
        ctx,
        changed,
        sourceObservation={**_SOURCE_OBSERVATION, "contentSha256": "changed-source"},
    )

    assert memory.revisionId(bucketAddress) == 2
    assert memory.revisionId(aggregateAddress) == 2
    assert [entry["kind"] for entry in result["secondAggregates"][0]["value"]["entries"]] == [
        "message",
        "message",
    ]



def test_identical_message_burst_persists_across_consecutive_seconds():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(70, "alice: GIGAEVIL", streamTimeSeconds=30.0, streamTime="00:00:30"),
        _raw(71, "bob: GIGAEVIL", streamTimeSeconds=30.0, streamTime="00:00:30"),
        _raw(72, "charlie: GIGAEVIL", streamTimeSeconds=31.0, streamTime="00:00:31"),
    ]

    result = _interpret(
        ctx,
        records,
        contextStartSeconds=29.0,
        contextEndSeconds=33.0,
    )

    assert len(result["identicalMessageBursts"]) == 1
    burst = result["identicalMessageBursts"][0]
    value = burst["value"]
    assert value["kind"] == "identicalMessageBurst"
    assert value["startSecond"] == 30
    assert value["endSecond"] == 31
    assert value["durationSeconds"] == 2
    assert value["messageCount"] == 3
    assert value["uniqueSourceUserCount"] == 3
    assert value["peakMessagesPerSecond"] == 2
    assert [occurrence["lineNumber"] for occurrence in value["occurrences"]] == [70, 71, 72]
    assert memory.revisionId(burst["address"]) == 1


def test_identical_message_burst_does_not_bridge_empty_second_gap():
    result = _interpret(
        _Ctx(),
        [
            _raw(73, "alice: GIGAEVIL", streamTimeSeconds=40.0, streamTime="00:00:40"),
            _raw(74, "bob: GIGAEVIL", streamTimeSeconds=42.0, streamTime="00:00:42"),
        ],
        contextStartSeconds=39.0,
        contextEndSeconds=44.0,
    )

    assert result["identicalMessageBursts"] == []


@pytest.mark.parametrize(
    ("contextStartSeconds", "contextEndSeconds"),
    [
        (50.0, 53.0),
        (49.0, 52.0),
    ],
)
def test_identical_message_burst_is_not_persisted_when_boundary_is_unproven(
    contextStartSeconds,
    contextEndSeconds,
):
    memory = CommittedValueLayer()
    result = _interpret(
        _Ctx(memory),
        [
            _raw(75, "alice: GIGAEVIL", streamTimeSeconds=50.0, streamTime="00:00:50"),
            _raw(76, "bob: GIGAEVIL", streamTimeSeconds=51.0, streamTime="00:00:51"),
        ],
        contextStartSeconds=contextStartSeconds,
        contextEndSeconds=contextEndSeconds,
    )

    assert result["identicalMessageBursts"] == []


def test_identical_message_burst_reuses_when_aggregate_inputs_are_unchanged():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(77, "alice: GIGAEVIL", streamTimeSeconds=60.0, streamTime="00:01:00"),
        _raw(78, "bob: GIGAEVIL", streamTimeSeconds=61.0, streamTime="00:01:01"),
    ]

    first = _interpret(
        ctx,
        records,
        contextStartSeconds=59.0,
        contextEndSeconds=63.0,
    )
    burstAddress = first["identicalMessageBursts"][0]["address"]
    assert memory.revisionId(burstAddress) == 1

    second = _interpret(
        ctx,
        records,
        contextStartSeconds=59.0,
        contextEndSeconds=63.0,
    )

    assert memory.revisionId(burstAddress) == 1
    assert second["identicalMessageBursts"][0]["dependency"] == first["identicalMessageBursts"][0]["dependency"]


def test_identical_message_burst_survives_save_bundle_rehydration():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(79, "alice: GIGAEVIL", streamTimeSeconds=70.0, streamTime="00:01:10"),
        _raw(80, "bob: GIGAEVIL", streamTimeSeconds=71.0, streamTime="00:01:11"),
    ]
    first = _interpret(
        ctx,
        records,
        contextStartSeconds=69.0,
        contextEndSeconds=73.0,
    )
    burstAddress = first["identicalMessageBursts"][0]["address"]

    bundle = SaveBundle.create(applicationId="evil-analysis", committedState=memory)
    restoredMemory = SaveBundle.fromBytes(bundle.toBytes()).restoreCommittedState()
    restored = _interpret(
        _Ctx(restoredMemory),
        records,
        contextStartSeconds=69.0,
        contextEndSeconds=73.0,
    )

    assert restoredMemory.revisionId(burstAddress) == 1
    assert restored["identicalMessageBursts"][0]["address"] == burstAddress



def test_burst_start_slot_replaces_old_event_when_source_extends_burst_backward():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)

    first = _interpret(
        ctx,
        [
            _raw(81, "alice: GIGAEVIL", streamTimeSeconds=80.0, streamTime="00:01:20"),
            _raw(82, "bob: GIGAEVIL", streamTimeSeconds=81.0, streamTime="00:01:21"),
        ],
        contextStartSeconds=79.0,
        contextEndSeconds=83.0,
    )
    oldSlot = chatSemantics._burstStartCellAddress(80)
    assert memory.revisionId(oldSlot) == 1
    assert first["identicalMessageBursts"][0]["address"] == oldSlot
    assert memory.load(oldSlot)["events"]

    second = _interpret(
        ctx,
        [
            _raw(80, "prior: GIGAEVIL", streamTimeSeconds=79.0, streamTime="00:01:19"),
            _raw(81, "alice: GIGAEVIL", streamTimeSeconds=80.0, streamTime="00:01:20"),
            _raw(82, "bob: GIGAEVIL", streamTimeSeconds=81.0, streamTime="00:01:21"),
        ],
        contextStartSeconds=78.0,
        contextEndSeconds=83.0,
        sourceObservation={**_SOURCE_OBSERVATION, "contentSha256": "extended-backward"},
    )

    newSlot = chatSemantics._burstStartCellAddress(79)
    assert memory.revisionId(newSlot) == 1
    assert second["identicalMessageBursts"][0]["address"] == newSlot
    assert memory.revisionId(oldSlot) == 2
    assert memory.load(oldSlot) == {
        "startSecond": 80,
        "events": [],
    }


def test_burst_start_slot_becomes_empty_when_burst_is_removed():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    _interpret(
        ctx,
        [
            _raw(90, "alice: GIGAEVIL", streamTimeSeconds=90.0, streamTime="00:01:30"),
            _raw(91, "bob: GIGAEVIL", streamTimeSeconds=91.0, streamTime="00:01:31"),
        ],
        contextStartSeconds=89.0,
        contextEndSeconds=93.0,
    )
    slot = chatSemantics._burstStartCellAddress(90)
    assert memory.revisionId(slot) == 1

    result = _interpret(
        ctx,
        [
            _raw(90, "alice: GIGAEVIL", streamTimeSeconds=90.0, streamTime="00:01:30"),
            _raw(91, "bob: Clap", streamTimeSeconds=91.0, streamTime="00:01:31"),
        ],
        contextStartSeconds=89.0,
        contextEndSeconds=93.0,
        sourceObservation={**_SOURCE_OBSERVATION, "contentSha256": "burst-removed"},
    )

    assert result["identicalMessageBursts"] == []
    assert memory.revisionId(slot) == 2
    assert memory.load(slot) == {
        "startSecond": 90,
        "events": [],
    }


def test_open_burst_invalidates_prior_closed_start_slot_without_revision_churn():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    _interpret(
        ctx,
        [
            _raw(100, "alice: GIGAEVIL", streamTimeSeconds=100.0, streamTime="00:01:40"),
            _raw(101, "bob: GIGAEVIL", streamTimeSeconds=101.0, streamTime="00:01:41"),
        ],
        contextStartSeconds=99.0,
        contextEndSeconds=103.0,
    )
    slot = chatSemantics._burstStartCellAddress(100)
    assert memory.revisionId(slot) == 1

    partial = [
        _raw(100, "alice: GIGAEVIL", streamTimeSeconds=100.0, streamTime="00:01:40"),
        _raw(101, "bob: GIGAEVIL", streamTimeSeconds=101.0, streamTime="00:01:41"),
        _raw(102, "charlie: GIGAEVIL", streamTimeSeconds=102.0, streamTime="00:01:42"),
    ]
    firstOpen = _interpret(
        ctx,
        partial,
        contextStartSeconds=99.0,
        contextEndSeconds=103.0,
        sourceObservation={**_SOURCE_OBSERVATION, "contentSha256": "open-extension"},
    )

    assert firstOpen["identicalMessageBursts"] == []
    assert memory.state(slot).value == "invalidated"
    assert memory.revisionId(slot) == 2

    secondOpen = _interpret(
        ctx,
        partial,
        contextStartSeconds=99.0,
        contextEndSeconds=103.0,
        sourceObservation={**_SOURCE_OBSERVATION, "contentSha256": "open-extension"},
    )

    assert secondOpen["identicalMessageBursts"] == []
    assert memory.state(slot).value == "invalidated"
    assert memory.revisionId(slot) == 2



def test_second_presentation_plan_keeps_exact_duplicate_group_above_semantic_pooling():
    result = _interpret(
        _Ctx(),
        [
            _raw(110, "alice: GIGAEVIL", streamTimeSeconds=110.0, streamTime="00:01:50"),
            _raw(111, "bob: GIGAEVIL", streamTimeSeconds=110.0, streamTime="00:01:50"),
        ],
        contextStartSeconds=109.0,
        contextEndSeconds=112.0,
    )

    plan = result["secondPresentations"][0]["value"]
    assert plan["secondIndex"] == 110
    assert plan["entries"] == [
        {
            "kind": "identicalMessageGroup",
            "canonicalMessage": "GIGAEVIL",
            "canonicalSpans": [
                {
                    "kind": "emote",
                    "name": "GIGAEVIL",
                    "count": 1,
                    "metadata": {
                        "semanticClass": "praise",
                        "classificationSource": "userDefined",
                    },
                }
            ],
            "lineNumbers": [110, 111],
            "semantic": [
                result["records"][0]["semanticValue"],
                result["records"][1]["semanticValue"],
            ],
            "sourceUsernames": ["alice", "bob"],
            "messageCount": 2,
            "uniqueSourceUserCount": 2,
        }
    ]


def test_second_presentation_plan_keeps_repeat_message_above_semantic_pooling():
    result = _interpret(
        _Ctx(),
        [
            _raw(
                112,
                "alice: GIGAEVIL ReallyGunPull Tutel GIGAEVIL ReallyGunPull Tutel",
                streamTimeSeconds=112.0,
                streamTime="00:01:52",
            )
        ],
    )

    entry = result["secondPresentations"][0]["value"]["entries"][0]
    assert entry["kind"] == "repeatMessage"
    assert entry["lineNumber"] == 112
    assert entry["count"] == 2
    assert entry["rendered"] == "(GIGAEVIL ReallyGunPull Tutel) x2"
    assert all(
        owner["kind"] != "semanticUnitGroup"
        for owner in result["secondPresentations"][0]["value"]["entries"]
    )


def test_second_presentation_plan_pools_trusted_semantic_units_across_messages():
    emotes = {
        **EMOTES,
        "EVILLOVE": {
            "semanticClass": "praise",
            "classificationSource": "userDefined",
        },
    }
    result = _interpret(
        _Ctx(emotes=emotes),
        [
            _raw(113, "alice: GIGAEVIL", streamTimeSeconds=113.0, streamTime="00:01:53"),
            _raw(114, "bob: EVILLOVE x2", streamTimeSeconds=113.0, streamTime="00:01:53"),
        ],
    )

    entries = result["secondPresentations"][0]["value"]["entries"]
    assert entries == [
        {
            "kind": "semanticUnitGroup",
            "meaning": {
                "semanticClass": "praise",
                "classificationSource": "userDefined",
            },
            "count": 3,
            "members": [
                {
                    "lineNumber": 113,
                    "count": 1,
                    "semantic": result["records"][0]["semanticValue"],
                },
                {
                    "lineNumber": 114,
                    "count": 2,
                    "semantic": result["records"][1]["semanticValue"],
                },
            ],
        }
    ]


def test_second_presentation_plan_keeps_unclassified_or_residual_text_individual():
    result = _interpret(
        _Ctx(),
        [
            _raw(115, "alice: Clap", streamTimeSeconds=115.0, streamTime="00:01:55"),
            _raw(
                116,
                "bob: GIGAEVIL holy shit",
                streamTimeSeconds=115.0,
                streamTime="00:01:55",
            ),
        ],
    )

    entries = result["secondPresentations"][0]["value"]["entries"]
    assert [entry["kind"] for entry in entries] == ["individual", "individual"]
    assert [entry["lineNumber"] for entry in entries] == [115, 116]


def test_second_presentation_plan_reuses_when_aggregate_and_burst_membership_are_unchanged():
    memory = CommittedValueLayer()
    ctx = _Ctx(memory)
    records = [
        _raw(117, "alice: GIGAEVIL", streamTimeSeconds=117.0, streamTime="00:01:57"),
        _raw(118, "bob: EVILLOVE", streamTimeSeconds=117.0, streamTime="00:01:57"),
    ]
    emotes = {
        **EMOTES,
        "EVILLOVE": {
            "semanticClass": "praise",
            "classificationSource": "userDefined",
        },
    }
    ctx = _Ctx(memory, emotes=emotes)
    first = _interpret(ctx, records)
    address = chatSemantics._secondPresentationAddress(117)
    assert memory.revisionId(address) == 1

    second = _interpret(ctx, records)

    assert memory.revisionId(address) == 1
    assert second["secondPresentations"][0]["dependency"] == first["secondPresentations"][0]["dependency"]



def test_second_presentation_plan_gives_closed_burst_precedence_over_lower_owners():
    result = _interpret(
        _Ctx(),
        [
            _raw(
                119,
                "alice: GIGAEVIL GIGAEVIL",
                streamTimeSeconds=119.0,
                streamTime="00:01:59",
            ),
            _raw(
                120,
                "bob: GIGAEVIL GIGAEVIL",
                streamTimeSeconds=120.0,
                streamTime="00:02:00",
            ),
        ],
        contextStartSeconds=118.0,
        contextEndSeconds=122.0,
    )

    assert len(result["identicalMessageBursts"]) == 1
    entries = [
        entry
        for plan in result["secondPresentations"]
        for entry in plan["value"]["entries"]
    ]
    assert [entry["kind"] for entry in entries] == [
        "burstOccurrence",
        "burstOccurrence",
    ]
    assert all(entry["burst"]["eventKey"] for entry in entries)


def test_second_presentation_plan_survives_save_bundle_rehydration():
    memory = CommittedValueLayer()
    emotes = {
        **EMOTES,
        "EVILLOVE": {
            "semanticClass": "praise",
            "classificationSource": "userDefined",
        },
    }
    records = [
        _raw(121, "alice: GIGAEVIL", streamTimeSeconds=121.0, streamTime="00:02:01"),
        _raw(122, "bob: EVILLOVE x2", streamTimeSeconds=121.0, streamTime="00:02:01"),
    ]
    first = _interpret(_Ctx(memory, emotes=emotes), records)
    address = chatSemantics._secondPresentationAddress(121)
    firstDependency = first["secondPresentations"][0]["dependency"]
    assert memory.revisionId(address) == 1

    bundle = SaveBundle.create(applicationId="evil-analysis", committedState=memory)
    restoredMemory = SaveBundle.fromBytes(bundle.toBytes()).restoreCommittedState()
    restored = _interpret(_Ctx(restoredMemory, emotes=emotes), records)

    assert restoredMemory.revisionId(address) == 1
    assert restored["secondPresentations"][0]["dependency"] == firstDependency
    assert restoredMemory.load(address) == first["secondPresentations"][0]["value"]
