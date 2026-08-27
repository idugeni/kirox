"""Tests for strict incremental EventStream decoding."""

import struct
import zlib

import pytest

from kirox.core.errors import StreamError
from kirox.core.eventstream import EventStreamDecoder, _read_headers, parse_eventstream


def encode_string_header(name: bytes, value: bytes) -> bytes:
    return bytes([len(name)]) + name + b"\x07" + struct.pack(">H", len(value)) + value


def create_test_message(name: bytes, value: bytes, body: bytes) -> bytes:
    headers = encode_string_header(name, value)
    total_length = 16 + len(headers) + len(body)
    prelude = struct.pack(">II", total_length, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message) & 0xFFFFFFFF)


def create_raw_message(headers: bytes, body: bytes = b"") -> bytes:
    total_length = 16 + len(headers) + len(body)
    prelude = struct.pack(">II", total_length, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message) & 0xFFFFFFFF)


def create_prelude(total_length: int, headers_length: int) -> bytes:
    prelude = struct.pack(">II", total_length, headers_length)
    return prelude + struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)


def test_string_header_compatibility() -> None:
    header = encode_string_header(b":event-type", b"test")
    assert _read_headers(header, 0, len(header))["event-type"].value == "test"


def test_parse_message_and_multiple_messages() -> None:
    first = create_test_message(b"event-type", b"test1", b'{"a":1}')
    second = create_test_message(b"event-type", b"test2", b'{"b":2}')

    messages = list(parse_eventstream(first + second))

    assert [message.event_type for message in messages] == ["test1", "test2"]
    assert messages[0].body == b'{"a":1}'


def test_decoder_handles_every_possible_chunk_boundary() -> None:
    first = create_test_message(b"event-type", b"test1", b'{"a":1}')
    second = create_test_message(b"event-type", b"test2", b'{"b":2}')
    wire_data = first + second
    decoder = EventStreamDecoder()
    messages = []

    for byte in wire_data:
        messages.extend(decoder.feed(bytes([byte])))
    decoder.finalize()

    assert [message.event_type for message in messages] == ["test1", "test2"]


def test_rejects_invalid_prelude_crc() -> None:
    message = bytearray(create_test_message(b"event-type", b"test", b"body"))
    message[8] ^= 0x01

    with pytest.raises(StreamError, match="prelude CRC"):
        list(parse_eventstream(bytes(message)))


def test_rejects_invalid_message_crc() -> None:
    message = bytearray(create_test_message(b"event-type", b"test", b"body"))
    message[-1] ^= 0x01

    with pytest.raises(StreamError, match="message CRC"):
        list(parse_eventstream(bytes(message)))


@pytest.mark.parametrize(
    ("prelude", "error"),
    [
        (create_prelude(15, 0), "total length"),
        (create_prelude(16, 1), "message bounds"),
        (create_prelude(65, 0), "64-byte limit"),
        (create_prelude(32, 9), "8-byte limit"),
    ],
)
def test_rejects_invalid_lengths(prelude: bytes, error: str) -> None:
    decoder = EventStreamDecoder(max_message_size=64, max_headers_size=8)

    with pytest.raises(StreamError, match=error):
        decoder.feed(prelude)


def test_rejects_unknown_and_truncated_header_types() -> None:
    unknown_type = bytes([1]) + b"x" + b"\xff"
    truncated_string = bytes([1]) + b"x" + b"\x07" + struct.pack(">H", 4) + b"ab"

    with pytest.raises(StreamError, match="Unsupported.*type"):
        list(parse_eventstream(create_raw_message(unknown_type)))
    with pytest.raises(StreamError, match="Truncated.*value"):
        list(parse_eventstream(create_raw_message(truncated_string)))


def test_parses_byte_array_timestamp_and_uuid_headers() -> None:
    byte_array = bytes([3]) + b"bin" + b"\x06" + struct.pack(">H", 2) + b"\x01\x02"
    timestamp = bytes([2]) + b"ts" + b"\x08" + struct.pack(">q", 1234)
    uuid_value = bytes(range(16))
    uuid_header = bytes([2]) + b"id" + b"\x09" + uuid_value

    message = list(parse_eventstream(create_raw_message(byte_array + timestamp + uuid_header)))[0]

    assert message.headers["bin"].value == b"\x01\x02"
    assert message.headers["ts"].value == 1234
    assert message.headers["id"].value == uuid_value.hex()


def test_finalize_rejects_truncated_frame_and_trailing_bytes() -> None:
    message = create_test_message(b"event-type", b"test", b"body")
    decoder = EventStreamDecoder()
    assert decoder.feed(message[:-1]) == []
    with pytest.raises(StreamError, match="Truncated EventStream message"):
        decoder.finalize()

    decoder = EventStreamDecoder()
    assert len(decoder.feed(message + b"x")) == 1
    with pytest.raises(StreamError, match="Truncated EventStream prelude"):
        decoder.finalize()


def test_feed_after_finalize_is_rejected() -> None:
    decoder = EventStreamDecoder()
    decoder.finalize()

    with pytest.raises(StreamError, match="already been finalized"):
        decoder.feed(b"")


def test_body_json_reports_corruption_as_a_stream_error() -> None:
    message = list(parse_eventstream(create_test_message(b"event-type", b"test", b"{oops")))[0]

    with pytest.raises(StreamError, match="test body is not valid JSON"):
        message.body_json()


def test_body_json_reports_invalid_utf8_as_a_stream_error() -> None:
    message = list(parse_eventstream(create_test_message(b"event-type", b"test", b'"\xff"')))[0]

    with pytest.raises(StreamError, match="not valid JSON"):
        message.body_json()


@pytest.mark.parametrize("body", [b"[]", b'"text"', b"7", b"null"])
def test_body_object_rejects_non_object_payloads(body: bytes) -> None:
    message = list(parse_eventstream(create_test_message(b"event-type", b"test", body)))[0]

    assert message.body_json() is not Ellipsis
    with pytest.raises(StreamError, match="test body must be a JSON object"):
        message.body_object()


def test_body_object_returns_the_decoded_mapping() -> None:
    message = list(parse_eventstream(create_test_message(b"event-type", b"test", b'{"a":1}')))[0]

    assert message.body_object() == {"a": 1}


def test_unnamed_event_errors_stay_readable() -> None:
    message = list(parse_eventstream(create_raw_message(b"", b"[]")))[0]

    assert message.event_type == ""
    with pytest.raises(StreamError, match="EventStream event body must be a JSON object"):
        message.body_object()
