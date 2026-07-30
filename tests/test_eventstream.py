"""Tests for eventstream."""

import struct
from kirox.core.eventstream import _read_headers, parse_eventstream


def create_test_message(name: bytes, value: bytes, body: bytes) -> bytes:
    headers = bytearray()
    headers.append(len(name))
    headers.extend(name)
    headers.append(7)
    headers.extend(struct.pack(">H", len(value)))
    headers.extend(value)
    total_len = 12 + len(headers) + len(body) + 4
    msg = struct.pack(">I", total_len)
    msg += struct.pack(">I", len(headers))
    msg += struct.pack(">I", 0)
    msg += bytes(headers)
    msg += body
    msg += struct.pack(">I", 0)
    return msg


def test_string_header():
    buf = bytearray()
    name, value = b"event-type", b"test"
    buf.append(len(name)); buf.extend(name); buf.append(7)
    buf.extend(struct.pack(">H", len(value))); buf.extend(value)
    assert _read_headers(bytes(buf), 0, len(buf))["event-type"].value == "test"


def test_parse_message():
    msg = create_test_message(b"event-type", b"test", b'{"content":"hi"}')
    msgs = list(parse_eventstream(msg))
    assert len(msgs) == 1
    assert msgs[0].event_type == "test"
    assert msgs[0].body == b'{"content":"hi"}'


def test_multiple_messages():
    msg1 = create_test_message(b"event-type", b"test1", b'{"a":1}')
    msg2 = create_test_message(b"event-type", b"test2", b'{"b":2}')
    msgs = list(parse_eventstream(msg1 + msg2))
    assert len(msgs) == 2
    assert msgs[0].event_type == "test1"
    assert msgs[1].event_type == "test2"
