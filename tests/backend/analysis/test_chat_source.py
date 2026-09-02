# file: tests/backend/analysis/test_chat_source.py
from __future__ import annotations

from datetime import datetime

import pytest

from backend.analysis.chatSource import (
    ChatSourceFormatError,
    parseChatLine,
    parseChatLines,
    selectChatWindow,
)


def test_parse_chat_line_preserves_message_verbatim() -> None:
    line = "[2026-08-31 21:03:04] #vedal987 Alice:  hello: world PogChamp"

    parsed = parseChatLine(line)

    assert parsed.timestamp == datetime(2026, 8, 31, 21, 3, 4)
    assert parsed.channel == "#vedal987"
    assert parsed.message == "Alice:  hello: world PogChamp"


def test_parse_chat_line_allows_empty_message() -> None:
    parsed = parseChatLine("[2026-08-31 21:03:04] #vedal987 ")

    assert parsed.message == ""


def test_parse_chat_line_does_not_interpret_non_user_message() -> None:
    message = "A moderator action happened without a username/body delimiter"

    parsed = parseChatLine(
        f"[2026-08-31 21:03:04] #vedal987 {message}",
    )

    assert parsed.message == message


def test_parse_chat_lines_removes_only_physical_line_terminator() -> None:
    parsed = parseChatLines(
        [
            "[2026-08-31 21:03:04] #vedal987 first  \n",
            "[2026-08-31 21:03:05] #vedal987  second\r\n",
        ],
    )

    assert tuple(line.message for line in parsed) == ("first  ", " second")


@pytest.mark.parametrize(
    "line",
    [
        "",
        "2026-08-31 21:03:04 #vedal987 message",
        "[2026-08-31 21:03:04]  #vedal987 message",
        "[2026-08-31 21:03:04]\t#vedal987 message",
        "[2026-08-31 21:03:04] #vedal987",
        "[2026-08-31 21:03:04] vedal987 message",
    ],
)
def test_parse_chat_line_rejects_invalid_fixed_structure(line: str) -> None:
    with pytest.raises(ChatSourceFormatError):
        parseChatLine(line)


def test_parse_chat_lines_reports_physical_line_number() -> None:
    with pytest.raises(ChatSourceFormatError, match="line 2"):
        parseChatLines(
            [
                "[2026-08-31 21:03:04] #vedal987 first\n",
                "bad line\n",
            ],
        )


def test_select_chat_window_uses_signed_stream_relative_time_without_clamping() -> None:
    streamStart = datetime(2026, 8, 31, 21, 0, 0)
    chatLines = parseChatLines(
        [
            "[2026-08-31 20:49:59] #vedal987 too early\n",
            "[2026-08-31 20:50:00] #vedal987 ten minutes before\n",
            "[2026-08-31 20:59:59] #vedal987 one second before\n",
            "[2026-08-31 21:00:00] #vedal987 stream start\n",
            "[2026-08-31 21:04:59] #vedal987 four fifty nine\n",
            "[2026-08-31 21:05:00] #vedal987 end boundary\n",
        ],
    )

    selected = selectChatWindow(
        chatLines,
        streamStart=streamStart,
        startSeconds=-600,
        endSeconds=300,
    )

    assert tuple(line.message for line in selected) == (
        "ten minutes before",
        "one second before",
        "stream start",
        "four fifty nine",
    )
