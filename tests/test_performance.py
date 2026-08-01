"""Performance tests."""

import concurrent.futures
import struct
import time
import zlib

from kirox.core.eventstream import parse_eventstream


def create_message() -> bytes:
    body = b'{"content": "test"}'
    name = b"event-type"
    headers = bytes([len(name)]) + name + b"\x07" + struct.pack(">H", 4) + b"test"
    total_length = 16 + len(headers) + len(body)
    prelude = struct.pack(">II", total_length, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message) & 0xFFFFFFFF)


def test_parse_performance() -> None:
    message = create_message()

    start = time.time()
    for _ in range(1000):
        list(parse_eventstream(message))
    elapsed = time.time() - start

    assert elapsed < 1.0, f"Too slow: {elapsed:.3f}s"


def test_concurrent_parse() -> None:
    message = create_message()

    def parse(count: int) -> int:
        for _ in range(count):
            list(parse_eventstream(message))
        return count

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(4) as executor:
        futures = [executor.submit(parse, 250) for _ in range(4)]
        total = sum(future.result() for future in futures)

    assert total == 1000
    assert (time.time() - start) < 2.0
