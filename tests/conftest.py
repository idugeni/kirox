"""Shared test fixtures."""

import struct


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
