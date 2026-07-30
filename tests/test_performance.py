"""Performance tests."""

import time
import struct
from kirox.core.eventstream import parse_eventstream


def test_parse_performance():
    body = b'{"content": "test"}'
    headers = bytearray()
    name = b"event-type"
    headers.append(len(name)); headers.extend(name); headers.append(7)
    headers.extend(struct.pack(">H", 4)); headers.extend(b"test")
    msg = struct.pack(">I", 12 + len(headers) + len(body) + 4)
    msg += struct.pack(">I", len(headers)) + struct.pack(">I", 0) + bytes(headers) + body + struct.pack(">I", 0)

    start = time.time()
    for _ in range(1000):
        list(parse_eventstream(msg))
    elapsed = time.time() - start
    assert elapsed < 1.0, f"Too slow: {elapsed:.3f}s"


def test_concurrent_parse():
    import concurrent.futures
    body = b'{"content": "test"}'
    headers = bytearray()
    name = b"event-type"
    headers.append(len(name)); headers.extend(name); headers.append(7)
    headers.extend(struct.pack(">H", 4)); headers.extend(b"test")
    msg = struct.pack(">I", 12 + len(headers) + len(body) + 4)
    msg += struct.pack(">I", len(headers)) + struct.pack(">I", 0) + bytes(headers) + body + struct.pack(">I", 0)

    def parse(n):
        for _ in range(n): list(parse_eventstream(msg))
        return n

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        total = sum(f.result() for f in [ex.submit(parse, 250) for _ in range(4)])
    assert (time.time() - start) < 2.0
