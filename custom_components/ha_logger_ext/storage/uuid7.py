from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate a UUID version 7 (time-ordered).

    Replace with uuid.uuid7() once HA requires Python 3.13+.

    Structure (RFC 9562):
      bits 0-47  : Unix timestamp in milliseconds
      bits 48-51 : version = 0b0111
      bits 52-63 : rand_a (12 random bits)
      bits 64-65 : variant = 0b10
      bits 66-127: rand_b (62 random bits)
    """
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits
    rand_a = (rand >> 68) & 0x0FFF               # top 12 bits
    rand_b = rand & 0x3FFFFFFFFFFFFFFF            # bottom 62 bits
    high = (ts_ms << 16) | (0x7 << 12) | rand_a
    low = (0b10 << 62) | rand_b
    return uuid.UUID(int=(high << 64) | low)
