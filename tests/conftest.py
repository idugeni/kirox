"""Shared test fixtures."""

import struct
import zlib


def create_test_message(name: bytes, value: bytes, body: bytes) -> bytes:
    headers = bytearray()
    headers.append(len(name))
    headers.extend(name)
    headers.append(7)
    headers.extend(struct.pack(">H", len(value)))
    headers.extend(value)
    total_length = 16 + len(headers) + len(body)
    prelude = struct.pack(">II", total_length, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + bytes(headers) + body
    return message + struct.pack(">I", zlib.crc32(message) & 0xFFFFFFFF)
