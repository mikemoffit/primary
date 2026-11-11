"""ISO 9141-2 message framing.

Request:   68 6A F1 <data...> <checksum>
Response:  48 6B <ecu> <data...> <checksum>

The checksum is the sum of all preceding bytes, modulo 256.  ISO 9141-2
frames carry no length byte -- on the wire, frames are separated by timing --
so when several frames arrive in one read we split them by header and
checksum instead.
"""

from __future__ import annotations

from dataclasses import dataclass

REQUEST_HEADER = bytes([0x68, 0x6A, 0xF1])
RESPONSE_HEADER = bytes([0x48, 0x6B])
MAX_DATA = 7  # ISO 9141-2 allows at most 7 data bytes per frame


@dataclass(frozen=True)
class Response:
    ecu: int | None
    data: bytes

    @property
    def mode(self) -> int:
        return self.data[0] if self.data else -1

    @property
    def is_negative(self) -> bool:
        return self.mode == 0x7F


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def build_request(data: bytes) -> bytes:
    if not 1 <= len(data) <= MAX_DATA:
        raise ValueError(f"request must be 1..{MAX_DATA} bytes, got {len(data)}")
    frame = REQUEST_HEADER + bytes(data)
    return frame + bytes([checksum(frame)])


def build_response(ecu: int, data: bytes) -> bytes:
    frame = RESPONSE_HEADER + bytes([ecu]) + bytes(data)
    return frame + bytes([checksum(frame)])


def split_responses(raw: bytes) -> list[Response]:
    """Split a burst of received bytes into checksum-valid response frames.

    Bytes that cannot be parsed (line noise, a stray echo) are skipped.
    """
    out: list[Response] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i:i + 2] != RESPONSE_HEADER:
            i += 1
            continue
        found = None
        # Smallest frame: 3 header bytes + 1 data byte + checksum = 5.
        for length in range(5, min(3 + MAX_DATA + 1, n - i) + 1):
            frame = raw[i:i + length]
            if checksum(frame[:-1]) != frame[-1]:
                continue
            nxt = i + length
            if nxt == n or raw[nxt:nxt + 2] == RESPONSE_HEADER:
                found = length
                break
            if found is None:
                found = length  # checksum OK but trailing junk; keep as fallback
        if found is None:
            i += 1
            continue
        frame = raw[i:i + found]
        out.append(Response(ecu=frame[2], data=bytes(frame[3:-1])))
        i += found
    return out
