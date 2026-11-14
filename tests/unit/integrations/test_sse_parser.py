from collections.abc import AsyncIterator

import pytest

from app.integrations.ai_mcp_service.exceptions import AiServiceProtocolError
from app.integrations.ai_mcp_service.schemas import ServerSentEvent
from app.integrations.ai_mcp_service.streaming import MAX_EVENT_CHARS, SSEParser, aiter_sse


def parse_all(*chunks: str) -> list[ServerSentEvent]:
    parser = SSEParser()
    events = [event for chunk in chunks for event in parser.feed(chunk)]
    return events + parser.close()


async def collect(*chunks: bytes) -> list[ServerSentEvent]:
    async def byte_stream() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    return [event async for event in aiter_sse(byte_stream())]


def test_parses_named_event_with_json_data() -> None:
    events = parse_all('event: generation.progress\ndata: {"completed":1,"total":7}\n\n')

    assert events == [
        ServerSentEvent(event="generation.progress", data='{"completed":1,"total":7}')
    ]
    assert events[0].json_data() == {"completed": 1, "total": 7}


def test_event_without_name_defaults_to_message() -> None:
    assert parse_all("data: hello\n\n")[0].event == "message"


def test_multiple_events_in_one_chunk() -> None:
    events = parse_all("event: a\ndata: 1\n\nevent: b\ndata: 2\n\n")

    assert [(e.event, e.data) for e in events] == [("a", "1"), ("b", "2")]


@pytest.mark.parametrize(
    "chunks",
    [
        ["event: stream.started\nda", 'ta: {"request_id":"r1"}\n', "\n"],
        ["e", "v", "ent: stream.started\n", 'data: {"request_id":"r1"}\n\n'],
        ["event: stream.started\r", '\ndata: {"request_id":"r1"}\r\n\r', "\n"],
    ],
)
def test_event_split_across_chunks_is_reassembled(chunks: list[str]) -> None:
    events = parse_all(*chunks)

    assert events == [ServerSentEvent(event="stream.started", data='{"request_id":"r1"}')]


def test_incomplete_event_is_buffered_until_blank_line() -> None:
    parser = SSEParser()

    assert parser.feed("data: partial\n") == []
    assert parser.feed("\n")[0].data == "partial"


def test_multi_line_data_is_joined_with_newlines() -> None:
    events = parse_all("data: Grilled hammour\ndata: with lemon rice\n\n")

    assert events[0].data == "Grilled hammour\nwith lemon rice"


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_all_line_ending_styles(newline: str) -> None:
    events = parse_all(f"event: x{newline}data: y{newline}{newline}")

    assert [(e.event, e.data) for e in events] == [("x", "y")]


def test_comments_and_heartbeats_produce_no_events() -> None:
    assert parse_all(": keep-alive\n\n", ":\n\n") == []


def test_only_one_leading_space_is_stripped_from_values() -> None:
    assert parse_all("data:  indented\n\n")[0].data == " indented"
    assert parse_all("data:no-space\n\n")[0].data == "no-space"


def test_id_persists_and_retry_is_parsed() -> None:
    events = parse_all("id: 41\nretry: 3000\ndata: a\n\ndata: b\n\n")

    assert (events[0].id, events[0].retry) == ("41", 3000)
    assert (events[1].id, events[1].retry) == ("41", None)


def test_unknown_fields_and_invalid_retry_are_ignored() -> None:
    events = parse_all("foo: bar\nretry: soon\ndata: ok\n\n")

    assert events == [ServerSentEvent(data="ok")]


def test_partial_event_at_end_of_stream_is_dropped() -> None:
    assert parse_all("data: never finished\n") == []


def test_leading_bom_is_ignored() -> None:
    assert parse_all("﻿data: x\n\n")[0].data == "x"


def test_oversized_line_without_newline_is_rejected() -> None:
    with pytest.raises(AiServiceProtocolError):
        SSEParser().feed("data: " + "a" * (MAX_EVENT_CHARS + 1))


def test_oversized_multi_line_event_is_rejected() -> None:
    line = "data: " + "a" * 1000 + "\n"
    with pytest.raises(AiServiceProtocolError):
        SSEParser().feed(line * (MAX_EVENT_CHARS // 1000 + 1))


async def test_utf8_character_split_across_byte_chunks_is_decoded() -> None:
    raw = "data: شاورما 🌯\n\n".encode()
    split_inside_emoji = raw.index("🌯".encode()) + 2

    events = await collect(raw[:split_inside_emoji], raw[split_inside_emoji:])

    assert events[0].data == "شاورما 🌯"


async def test_done_sentinel_ends_the_stream_and_is_not_yielded() -> None:
    events = await collect(b"data: one\n\ndata: [DONE]\n\ndata: after\n\n")

    assert [e.data for e in events] == ["one"]
