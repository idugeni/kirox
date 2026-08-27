"""Incremental decoder for the AWS EventStream binary protocol."""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Generator

from kirox.core.errors import StreamError

MIN_MESSAGE_SIZE = 16
MAX_MESSAGE_SIZE = 24 * 1024 * 1024
MAX_HEADERS_SIZE = 128 * 1024
_PRELUDE_SIZE = 12
_TRAILER_SIZE = 4
_HEADER_TYPE_NAMES = {
    0: "boolTrue",
    1: "boolFalse",
    2: "byte",
    3: "short",
    4: "integer",
    5: "long",
    6: "byteArray",
    7: "string",
    8: "timestamp",
    9: "uuid",
}


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
        header = self.headers.get("event-type")
        return header.value if header else ""

    @property
    def content_type(self) -> str:
        header = self.headers.get("content-type")
        return header.value if header else ""

    def body_json(self) -> Any:
        """Decode the body as JSON, reporting corruption as a stream failure.

        Upstream payload corruption is an upstream problem, so it must not
        escape as a bare `json.JSONDecodeError` that callers would classify as
        an internal error.
        """
        try:
            return json.loads(self.body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise StreamError(
                f"EventStream {self.event_type or 'event'} body is not valid JSON"
            ) from exc

    def body_object(self) -> dict[str, Any]:
        """Decode the body as a JSON object, rejecting any other JSON shape."""
        payload = self.body_json()
        if not isinstance(payload, dict):
            raise StreamError(
                f"EventStream {self.event_type or 'event'} body must be a JSON object"
            )
        return payload


def _require_header_bytes(position: int, size: int, end: int, description: str) -> None:
    if position + size > end:
        raise StreamError(f"Truncated EventStream header {description}")


def _read_headers(data: bytes, offset: int, length: int) -> dict[str, HeaderValue]:
    """Parse a bounded AWS EventStream header block.

    Header names retain the historical Kirox behaviour of stripping a leading
    colon, so ``:event-type`` remains accessible as ``event-type``.
    """
    if offset < 0 or length < 0 or offset + length > len(data):
        raise StreamError("EventStream header block is outside the message bounds")

    headers: dict[str, HeaderValue] = {}
    position = offset
    end = offset + length

    while position < end:
        _require_header_bytes(position, 1, end, "name length")
        name_length = data[position]
        position += 1
        if name_length == 0:
            raise StreamError("EventStream header name cannot be empty")

        _require_header_bytes(position, name_length, end, "name")
        try:
            name = data[position : position + name_length].decode("utf-8").lstrip(":")
        except UnicodeDecodeError as exc:
            raise StreamError("EventStream header name is not valid UTF-8") from exc
        position += name_length
        if not name:
            raise StreamError("EventStream header name cannot be empty")

        _require_header_bytes(position, 1, end, "type")
        raw_type = data[position]
        position += 1
        if raw_type not in _HEADER_TYPE_NAMES:
            raise StreamError(f"Unsupported EventStream header type {raw_type}")

        value: Any
        if raw_type == 0:
            value = True
        elif raw_type == 1:
            value = False
        elif raw_type == 2:
            _require_header_bytes(position, 1, end, "byte value")
            value = struct.unpack_from(">b", data, position)[0]
            position += 1
        elif raw_type == 3:
            _require_header_bytes(position, 2, end, "short value")
            value = struct.unpack_from(">h", data, position)[0]
            position += 2
        elif raw_type == 4:
            _require_header_bytes(position, 4, end, "integer value")
            value = struct.unpack_from(">i", data, position)[0]
            position += 4
        elif raw_type == 5:
            _require_header_bytes(position, 8, end, "long value")
            value = struct.unpack_from(">q", data, position)[0]
            position += 8
        elif raw_type in (6, 7):
            _require_header_bytes(position, 2, end, "value length")
            value_length = struct.unpack_from(">H", data, position)[0]
            position += 2
            _require_header_bytes(position, value_length, end, "value")
            raw_value = data[position : position + value_length]
            position += value_length
            if raw_type == 6:
                value = raw_value
            else:
                try:
                    value = raw_value.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise StreamError("EventStream string header is not valid UTF-8") from exc
        elif raw_type == 8:
            _require_header_bytes(position, 8, end, "timestamp value")
            value = struct.unpack_from(">q", data, position)[0]
            position += 8
        else:
            _require_header_bytes(position, 16, end, "UUID value")
            value = data[position : position + 16].hex()
            position += 16

        headers[name] = HeaderValue(
            raw_type=raw_type,
            type_name=_HEADER_TYPE_NAMES[raw_type],
            value=value,
        )

    return headers


class EventStreamDecoder:
    """Stateful, incremental AWS EventStream decoder."""

    def __init__(
        self,
        *,
        max_message_size: int = MAX_MESSAGE_SIZE,
        max_headers_size: int = MAX_HEADERS_SIZE,
    ) -> None:
        if max_message_size < MIN_MESSAGE_SIZE:
            raise ValueError(f"max_message_size must be at least {MIN_MESSAGE_SIZE}")
        if max_headers_size < 0:
            raise ValueError("max_headers_size cannot be negative")
        self._max_message_size = max_message_size
        self._max_headers_size = max_headers_size
        self._buffer = bytearray()
        self._finalized = False

    def feed(self, data: bytes | bytearray | memoryview) -> list[EventStreamMessage]:
        """Consume a chunk and return all complete messages decoded from it."""
        if self._finalized:
            raise StreamError("EventStream decoder has already been finalized")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("EventStream data must be bytes-like")

        self._buffer.extend(data)
        messages: list[EventStreamMessage] = []

        while len(self._buffer) >= _PRELUDE_SIZE:
            total_length, headers_length, expected_prelude_crc = struct.unpack_from(
                ">III", self._buffer
            )
            self._validate_lengths(total_length, headers_length)

            actual_prelude_crc = zlib.crc32(self._buffer[:8]) & 0xFFFFFFFF
            if actual_prelude_crc != expected_prelude_crc:
                raise StreamError("EventStream prelude CRC mismatch")

            if len(self._buffer) < total_length:
                break

            frame = bytes(self._buffer[:total_length])
            expected_message_crc = struct.unpack_from(">I", frame, total_length - 4)[0]
            actual_message_crc = zlib.crc32(frame[:-4]) & 0xFFFFFFFF
            if actual_message_crc != expected_message_crc:
                raise StreamError("EventStream message CRC mismatch")

            headers = _read_headers(frame, _PRELUDE_SIZE, headers_length)
            body_start = _PRELUDE_SIZE + headers_length
            messages.append(
                EventStreamMessage(
                    total_length=total_length,
                    headers=headers,
                    body=frame[body_start : total_length - _TRAILER_SIZE],
                )
            )
            del self._buffer[:total_length]

        return messages

    def finalize(self) -> None:
        """Finish decoding and reject any incomplete trailing frame."""
        if self._finalized:
            return
        self._finalized = True
        if not self._buffer:
            return

        if len(self._buffer) < _PRELUDE_SIZE:
            raise StreamError(
                f"Truncated EventStream prelude: {len(self._buffer)} trailing byte(s)"
            )
        total_length = struct.unpack_from(">I", self._buffer)[0]
        raise StreamError(
            "Truncated EventStream message: "
            f"expected {total_length} bytes, received {len(self._buffer)}"
        )

    def _validate_lengths(self, total_length: int, headers_length: int) -> None:
        if total_length < MIN_MESSAGE_SIZE:
            raise StreamError(
                f"EventStream total length {total_length} is smaller than {MIN_MESSAGE_SIZE}"
            )
        if total_length > self._max_message_size:
            raise StreamError(
                f"EventStream total length {total_length} exceeds "
                f"the {self._max_message_size}-byte limit"
            )
        if headers_length > self._max_headers_size:
            raise StreamError(
                f"EventStream headers length {headers_length} exceeds "
                f"the {self._max_headers_size}-byte limit"
            )
        maximum_headers_length = total_length - _PRELUDE_SIZE - _TRAILER_SIZE
        if headers_length > maximum_headers_length:
            raise StreamError(f"EventStream headers length {headers_length} exceeds message bounds")


def parse_eventstream(data: bytes) -> Generator[EventStreamMessage, None, None]:
    """Parse complete EventStream bytes using the incremental decoder."""
    decoder = EventStreamDecoder()
    yield from decoder.feed(data)
    decoder.finalize()
