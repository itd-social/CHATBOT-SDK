"""UUIDv7 generation for Python 3.10+, following RFC 9562 section 5.7."""

import secrets
import time
import uuid


def _uuid7() -> str:
    timestamp = time.time_ns() // 1_000_000
    if not 0 <= timestamp < 1 << 48:
        raise ValueError("System time cannot be represented as UUIDv7")
    random = secrets.randbits(74)
    value = (
        timestamp << 80
        | 7 << 76
        | (random >> 62) << 64
        | 2 << 62
        | (random & ((1 << 62) - 1))
    )
    return str(uuid.UUID(int=value))
