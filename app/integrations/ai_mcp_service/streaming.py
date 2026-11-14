"""Incremental Server-Sent Events parser (WHATWG HTML "event stream" interpretation).

Network chunks are arbitrary: one chunk may hold several events, part of an event, or half of a
multi-byte UTF-8 character. Lines may end in CRLF, LF or CR; comments (`:`) are heartbeats.
"""

import codecs
import re
from collections.abc import AsyncIterable, AsyncIterator, Iterator

from app.integrations.ai_mcp_service.exceptions import AiServiceProtocolError
from app.integrations.ai_mcp_service.schemas import (
    DEFAULT_EVENT_TYPE,
    DONE_SENTINEL,
    ServerSentEvent,
)

# Upper bound on one buffered line or event; a stream exceeding it is rejected, not buffered.
MAX_EVENT_CHARS = 1_000_000
_LINE_BREAK = re.compile(r"[\r\n]")
_BOM = "﻿"


class SSEParser:
    def __init__(self) -> None:
        self._buffer = ""
        self._at_stream_start = True
        self._data_lines: list[str] = []
        self._data_chars = 0
        self._event_type: str | None = None
        self._retry: int | None = None
        self._last_event_id: str | None = None

    def feed(self, text: str) -> list[ServerSentEvent]:
        """Add decoded text; return every event completed by it (possibly none)."""
        if self._at_stream_start and text:
            text = text.removeprefix(_BOM)
            self._at_stream_start = False
        self._buffer += text
        events = [event for line in self._complete_lines() if (event := self._process(line))]
        if len(self._buffer) > MAX_EVENT_CHARS:
            raise AiServiceProtocolError("SSE line exceeds the maximum size")
        return events

    def close(self) -> list[ServerSentEvent]:
        """End of stream. A trailing CR is a line end; an undispatched partial event is dropped."""
        if not self._buffer.endswith("\r"):
            return []
        self._buffer += "\n"
        return [event for line in self._complete_lines() if (event := self._process(line))]

    def _complete_lines(self) -> Iterator[str]:
        while match := _LINE_BREAK.search(self._buffer):
            index = match.start()
            if self._buffer[index] == "\r":
                if index + 1 == len(self._buffer):
                    return  # Might be the first half of a CRLF split across chunks.
                end = index + 2 if self._buffer[index + 1] == "\n" else index + 1
            else:
                end = index + 1
            line, self._buffer = self._buffer[:index], self._buffer[end:]
            yield line

    def _process(self, line: str) -> ServerSentEvent | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "data":
            self._data_chars += len(value) + 1
            if self._data_chars > MAX_EVENT_CHARS:
                raise AiServiceProtocolError("SSE event exceeds the maximum size")
            self._data_lines.append(value)
        elif field == "event":
            self._event_type = value
        elif field == "id" and "\0" not in value:
            self._last_event_id = value
        elif field == "retry" and value.isascii() and value.isdigit():
            self._retry = int(value)
        return None

    def _dispatch(self) -> ServerSentEvent | None:
        event = None
        if self._data_lines:
            event = ServerSentEvent(
                event=self._event_type or DEFAULT_EVENT_TYPE,
                data="\n".join(self._data_lines),
                id=self._last_event_id,
                retry=self._retry,
            )
        self._data_lines, self._data_chars = [], 0
        self._event_type, self._retry = None, None
        return event


async def aiter_sse(chunks: AsyncIterable[bytes]) -> AsyncIterator[ServerSentEvent]:
    """Yield events from a byte stream, stopping at the `[DONE]` sentinel or end of stream."""
    parser = SSEParser()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    async for chunk in chunks:
        for event in parser.feed(decoder.decode(chunk)):
            if event.data == DONE_SENTINEL:
                return
            yield event
    for event in [*parser.feed(decoder.decode(b"", final=True)), *parser.close()]:
        if event.data == DONE_SENTINEL:
            return
        yield event
