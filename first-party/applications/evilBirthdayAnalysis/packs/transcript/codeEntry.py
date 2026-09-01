from __future__ import annotations

import math

_sourceCache: dict[str, object] = {}


def _timeSeconds(value: str, *, fieldName: str) -> float:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid HH:MM:SS {fieldName}: {value!r}.")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as err:
        raise ValueError(f"Invalid HH:MM:SS {fieldName}: {value!r}.") from err
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Invalid HH:MM:SS {fieldName}: {value!r}.")
    return float(hours * 3600 + minutes * 60 + seconds)


def _formatStreamTime(seconds: float) -> str:
    if not math.isfinite(seconds):
        raise ValueError("Stream-relative timestamp must be finite.")
    wholeSeconds = math.floor(seconds)
    sign = "-" if wholeSeconds < 0 else ""
    absolute = abs(wholeSeconds)
    hours, remainder = divmod(absolute, 3600)
    minutes, secondsValue = divmod(remainder, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{secondsValue:02d}"


def _finiteSeconds(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if type(value) not in {int, float}:
        raise TypeError(f"Transcript selector {key!r} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Transcript selector {key!r} must be finite.")
    return result


def _source(ctx, transcriptPath: str) -> object:
    if transcriptPath not in _sourceCache:
        _sourceCache[transcriptPath] = ctx.io.readJson(transcriptPath)
    return _sourceCache[transcriptPath]


def _select(ctx, payload):
    if not isinstance(payload, dict):
        raise ValueError("Transcript selection requires an object payload.")
    transcriptPath = ctx.config.get("transcriptFile")
    streamStartTime = ctx.config.get("streamStartTime")
    if type(transcriptPath) is not str:
        raise ValueError("Application config transcriptFile must be a string path.")
    if type(streamStartTime) is not str:
        raise ValueError("Application config streamStartTime must be an HH:MM:SS video offset.")

    startVideo = _finiteSeconds(payload, "videoStartSeconds")
    endVideo = _finiteSeconds(payload, "videoEndSeconds")
    if endVideo < startVideo:
        raise ValueError("Transcript selector produced an inverted video-time window.")

    streamStartSeconds = _timeSeconds(streamStartTime, fieldName="streamStartTime")
    startTranscript = startVideo - streamStartSeconds
    endTranscript = endVideo - streamStartSeconds

    source = _source(ctx, transcriptPath)
    if not isinstance(source, dict):
        raise ValueError("Transcript JSON root must be an object.")
    segments = source.get("segments")
    if not isinstance(segments, list):
        raise ValueError("Transcript JSON must contain segments[].")

    selectedSegments: list[dict[str, object]] = []
    renderedSegments: list[str] = []
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
            if endValue > startTranscript and startValue < endTranscript:
                selectedWords.append({"word": text, "start": startValue, "end": endValue})
        if selectedWords:
            firstWordStart = float(selectedWords[0]["start"])
            segmentText = " ".join(str(word["word"]) for word in selectedWords)
            selectedSegments.append(
                {
                    "segmentIndex": segmentIndex,
                    "streamStartSeconds": firstWordStart,
                    "streamStartTime": _formatStreamTime(firstWordStart),
                    "words": selectedWords,
                }
            )
            renderedSegments.append(f"{_formatStreamTime(firstWordStart)} {segmentText}")

    return {
        "sourcePath": transcriptPath,
        "streamStartTime": streamStartTime,
        "streamStartVideoSeconds": streamStartSeconds,
        "videoStartSeconds": startVideo,
        "videoEndSeconds": endVideo,
        "transcriptStartSeconds": startTranscript,
        "transcriptEndSeconds": endTranscript,
        "segments": selectedSegments,
        "text": "\n".join(renderedSegments),
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.transcript@1", _select)
