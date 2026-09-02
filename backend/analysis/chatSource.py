# file: backend/analysis/chatSource.py ; version: 1
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

__all__: list[str] = [
    "ChatLine",
    "ChatSourceFormatError",
    "parseChatLine",
    "parseChatLines",
    "selectChatWindow",
]


_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_TIMESTAMP_TEXT_LENGTH = 19
_PREFIX_LENGTH = 22  # '[' + timestamp + '] ' + at least one channel char


class ChatSourceFormatError(ValueError):
    """Raised when one physical chat-source line violates its fixed record format."""


@dataclass(frozen=True, slots=True)
class ChatLine:
    """
    Represents one minimally parsed physical record from a chat source file.

    The source contract is::

        [YYYY-MM-DD HH:mm:SS] #channel message

    Ingestion deliberately interprets only the timestamp, channel token, and
    remaining message text. The message is retained verbatim after the second
    structural ASCII-space separator. It is not split into username/body and
    is not classified as user, moderation, information, or system content.

    timestamp is a timezone-naive wall-clock datetime because the source format
    carries no timezone. Code aligning records to a stream must therefore pass
    a streamStart using the same clock convention.
    """

    timestamp: datetime
    channel: str
    message: str

    def relativeSeconds(self, *, streamStart: datetime) -> float:
        """Returns this record's signed offset in seconds from streamStart."""
        if not isinstance(streamStart, datetime):
            raise TypeError(
                "streamStart must be datetime; "
                f"received: {type(streamStart).__name__}.",
            )

        if self.timestamp.tzinfo is None and streamStart.tzinfo is not None:
            raise ValueError(
                "streamStart must use the same timezone convention as chat "
                "timestamps; chat timestamps are timezone-naive.",
            )

        if self.timestamp.tzinfo is not None and streamStart.tzinfo is None:
            raise ValueError(
                "streamStart must use the same timezone convention as chat "
                "timestamps.",
            )

        return (self.timestamp - streamStart).total_seconds()


def parseChatLine(line: str) -> ChatLine:
    """
    Parses one physical chat-source line without interpreting message contents.

    The two separators outside the timestamp are exactly one ASCII space:
    one after the closing bracket and one after the channel token. Additional
    characters after the second separator, including leading spaces, colons,
    Unicode, and emote text, belong to message and are preserved verbatim.

    The input must not contain a trailing newline; parseChatLines() removes only
    physical line terminators before delegating here.
    """
    if type(line) is not str:
        raise TypeError(
            f"line must be exact str; received: {type(line).__name__}.",
        )

    if "\n" in line or "\r" in line:
        raise ChatSourceFormatError(
            "line must contain exactly one physical record without a line terminator.",
        )

    if len(line) < _PREFIX_LENGTH:
        raise ChatSourceFormatError("chat line is too short to contain a valid record.")

    if line[0] != "[" or line[20:22] != "] ":
        raise ChatSourceFormatError(
            "chat line must begin with '[YYYY-MM-DD HH:mm:SS] '.",
        )

    timestampText = line[1:20]
    try:
        timestamp = datetime.strptime(timestampText, _TIMESTAMP_FORMAT)
    except ValueError as err:
        raise ChatSourceFormatError(
            f"invalid chat timestamp: {timestampText!r}.",
        ) from err

    channelStart = 22
    separatorIndex = line.find(" ", channelStart)
    if separatorIndex < 0:
        raise ChatSourceFormatError(
            "chat line must contain one ASCII space after the channel token.",
        )

    channel = line[channelStart:separatorIndex]
    if not channel:
        raise ChatSourceFormatError("chat channel token must not be empty.")
    if not channel.startswith("#"):
        raise ChatSourceFormatError(
            f"chat channel token must start with '#'; received: {channel!r}.",
        )
    if any(character.isspace() for character in channel):
        raise ChatSourceFormatError(
            f"chat channel token must not contain whitespace; received: {channel!r}.",
        )

    message = line[separatorIndex + 1 :]
    return ChatLine(
        timestamp=timestamp,
        channel=channel,
        message=message,
    )


def parseChatLines(lines: Iterable[str]) -> tuple[ChatLine, ...]:
    """
    Parses chat records from physical text-file lines in source order.

    Empty physical lines are invalid records rather than silently ignored.
    Only the final CR/LF line terminator is removed; all message content is
    otherwise preserved exactly.
    """
    parsed: list[ChatLine] = []

    for lineNumber, physicalLine in enumerate(lines, start=1):
        if type(physicalLine) is not str:
            raise TypeError(
                "chat source lines must be exact str values; "
                f"lineNumber={lineNumber}, received: {type(physicalLine).__name__}.",
            )

        line = physicalLine.removesuffix("\n").removesuffix("\r")
        try:
            parsed.append(parseChatLine(line))
        except ChatSourceFormatError as err:
            raise ChatSourceFormatError(
                f"invalid chat source record at line {lineNumber}: {err}",
            ) from err

    return tuple(parsed)


def selectChatWindow(
    chatLines: Iterable[ChatLine],
    *,
    streamStart: datetime,
    startSeconds: float,
    endSeconds: float,
) -> tuple[ChatLine, ...]:
    """
    Returns records in the half-open stream-relative interval [start, end).

    Negative offsets are valid and intentionally preserve pre-stream chat.
    Source order is retained. No clamping is performed when the requested
    interval extends outside available chat data.
    """
    if not isinstance(startSeconds, int | float) or isinstance(startSeconds, bool):
        raise TypeError(
            "startSeconds must be int or float; "
            f"received: {type(startSeconds).__name__}.",
        )
    if not isinstance(endSeconds, int | float) or isinstance(endSeconds, bool):
        raise TypeError(
            "endSeconds must be int or float; "
            f"received: {type(endSeconds).__name__}.",
        )
    if endSeconds < startSeconds:
        raise ValueError("endSeconds must be greater than or equal to startSeconds.")

    selected: list[ChatLine] = []
    for chatLine in chatLines:
        if not isinstance(chatLine, ChatLine):
            raise TypeError(
                "chatLines must contain ChatLine values; "
                f"received: {type(chatLine).__name__}.",
            )

        relativeSeconds = chatLine.relativeSeconds(streamStart=streamStart)
        if startSeconds <= relativeSeconds < endSeconds:
            selected.append(chatLine)

    return tuple(selected)
