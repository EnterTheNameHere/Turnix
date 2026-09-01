# file: first-party/applications/evilBirthdayAnalysis/packs/transcript/codeEntry.py ; version: 2
from __future__ import annotations

import math


def _timeSeconds(value: str) -> float:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid HH:MM:SS anchor: {value!r}.")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as err:
        raise ValueError(f"Invalid HH:MM:SS anchor: {value!r}.") from err
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Invalid HH:MM:SS anchor: {value!r}.")
    return float(hours * 3600 + minutes * 60 + seconds)


def _nonNegativeSeconds(settings: dict[str, object], key: str, default: float) -> float:
    value = settings.get(key, default)
    if type(value) not in {int, float}:
        raise TypeError(f"Profile setting {key!r} must be numeric.")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Profile setting {key!r} must be a finite non-negative number.")
    return result


def _window(profile: dict[str, object]) -> tuple[float, float]:
    name = profile["name"]
    settings = profile["settings"]
    if not isinstance(settings, dict):
        raise ValueError("Profile settings must be an object.")
    match name:
        case "0-10-30-profile":
            anchorValue = settings.get("anchor")
            if type(anchorValue) is not str:
                raise ValueError("0-10-30-profile requires a string anchor setting.")
            anchor = _timeSeconds(anchorValue)
            start = anchor - _nonNegativeSeconds(settings, "transcriptBeforeSeconds", 0.0)
            end = anchor + _nonNegativeSeconds(settings, "transcriptAfterSeconds", 600.0)
            if start > end:
                raise ValueError("Transcript profile produced an inverted time window.")
            return start, end
        case _:
            raise ValueError(f"Transcript Pack does not support profile {name!r}.")


def _select(ctx, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("profile"), dict):
        raise ValueError("Transcript selection requires a profile snapshot.")
    transcriptPath = ctx.config.get("transcriptFile")
    if type(transcriptPath) is not str:
        raise ValueError("Application config transcriptFile must be a string path.")
    source = ctx.io.readJson(transcriptPath)
    segments = source.get("segments")
    if not isinstance(segments, list):
        raise ValueError("Transcript JSON must contain segments[].")
    startSeconds, endSeconds = _window(payload["profile"])
    selectedSegments: list[dict[str, object]] = []
    for segmentIndex, segment in enumerate(segments):
        if not isinstance(segment, dict) or not isinstance(segment.get("words"), list):
            raise ValueError(f"Transcript segment {segmentIndex} must contain words[].")
        selectedWords: list[dict[str, object]] = []
        for wordIndex, word in enumerate(segment["words"]):
            if not isinstance(word, dict):
                raise ValueError(f"Transcript word {segmentIndex}:{wordIndex} must be an object.")
            text, start, end = word.get("word"), word.get("start"), word.get("end")
            if type(text) is not str or type(start) not in {int, float} or type(end) not in {int, float}:
                raise ValueError(f"Transcript word {segmentIndex}:{wordIndex} has invalid word/start/end fields.")
            startValue, endValue = float(start), float(end)
            if not math.isfinite(startValue) or not math.isfinite(endValue) or startValue < 0 or endValue < startValue:
                raise ValueError(f"Transcript word {segmentIndex}:{wordIndex} has invalid timing order.")
            if endValue >= startSeconds and startValue <= endSeconds:
                selectedWords.append({"word": text, "start": startValue, "end": endValue})
        if selectedWords:
            selectedSegments.append({"segmentIndex": segmentIndex, "words": selectedWords})
    text = "".join(word["word"] for segment in selectedSegments for word in segment["words"])
    return {
        "sourcePath": transcriptPath,
        "startSeconds": startSeconds,
        "endSeconds": endSeconds,
        "segments": selectedSegments,
        "text": text,
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.transcript@1", _select)
