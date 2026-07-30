"""EventStream binary protocol parser."""

from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import Any, Generator


@dataclass(frozen=True)
class HeaderValue:
    raw_type: int
    type_name: str
    value: Any


@dataclass
class EventStreamMessage:
    total_length: int
    headers: dict[str, HeaderValue] = field(default_factory=dict)
    body: bytes = b""

    @property
    def event_type(self) -> str:
        h = self.headers.get("event-type")
        return h.value if h else ""

    @property
    def content_type(self) -> str:
        h = self.headers.get("content-type")
        return h.value if h else ""

    def body_json(self) -> Any:
        import json
        return json.loads(self.body)


def _read_headers(data: bytes, offset: int, length: int) -> dict[str, HeaderValue]:
    headers: dict[str, HeaderValue] = {}
    pos, end = offset, offset + length
    type_names = {0: "boolTrue", 1: "boolFalse", 2: "byte", 3: "short", 4: "integer", 5: "long", 6: "timestamp", 7: "string", 8: "uuid"}

    try:
        while pos < end:
            if pos + 1 > end: break
            name_len = data[pos]; pos += 1
            if pos + name_len > end: break
            name = data[pos:pos + name_len].decode("utf-8", errors="replace").lstrip(":"); pos += name_len
            if pos >= end: break
            htype = data[pos]; pos += 1

            if htype == 0: val = True
            elif htype == 1: val = False
            elif htype == 2:
                if pos + 1 > end: break
                val = struct.unpack_from(">b", data, pos)[0]; pos += 1
            elif htype == 3:
                if pos + 2 > end: break
                val = struct.unpack_from(">h", data, pos)[0]; pos += 2
            elif htype == 4:
                if pos + 4 > end: break
                val = struct.unpack_from(">i", data, pos)[0]; pos += 4
            elif htype == 5:
                if pos + 8 > end: break
                val = struct.unpack_from(">q", data, pos)[0]; pos += 8
            elif htype == 6:
                if pos + 8 > end: break
                val = struct.unpack_from(">q", data, pos)[0]; pos += 8
            elif htype == 7:
                if pos + 2 > end: break
                slen = struct.unpack_from(">H", data, pos)[0]; pos += 2
                val = data[pos:pos + slen].decode("utf-8", errors="replace"); pos += slen
            elif htype == 8:
                if pos + 16 > end: break
                val = data[pos:pos + 16].hex(); pos += 16
            else: val = f"<unknown {htype}>"
            headers[name] = HeaderValue(raw_type=htype, type_name=type_names.get(htype, "?"), value=val)
    except (IndexError, struct.error):
        pass
    return headers


def parse_eventstream(data: bytes) -> Generator[EventStreamMessage, None, None]:
    from kirox.core.errors import StreamError
    pos = 0
    while pos + 16 <= len(data):
        total_len = struct.unpack_from(">I", data, pos)[0]
        if total_len < 16 or pos + total_len > len(data):
            raise StreamError(f"Invalid message: len={total_len}")
        header_len = struct.unpack_from(">I", data, pos + 4)[0]
        body = data[pos + 12 + header_len:pos + total_len - 4]
        headers = _read_headers(data, pos + 12, header_len)
        yield EventStreamMessage(total_length=total_len, headers=headers, body=body)
        pos += total_len
