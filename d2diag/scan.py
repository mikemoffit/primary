"""Module discovery: try the 5-baud wake-up on every K-line address.

The Discovery II's non-engine modules (ABS, SLS, ACE, BCU, instruments,
SRS, HEVAC, gearbox) share the K-line but do not answer generic OBD-II
requests.  Rather than guess their addresses, sweep the address space and
record which ones wake up, with which keybytes, at which baud rate.

This only sends the wake-up handshake -- no commands -- so it is read-only.
"""

from __future__ import annotations

from typing import Callable, Iterable

from .kline import InitResult, KLineBackend, W0_BUS_IDLE


def scan(backend: KLineBackend, addresses: Iterable[int], bauds: Iterable[int] = (10400, 9600),
         progress: Callable[[InitResult], None] | None = None) -> list[InitResult]:
    found: list[InitResult] = []
    bauds = list(bauds)
    for addr in addresses:
        for baud in bauds:
            try:
                res = backend.slow_init(addr, baud)
            except Exception as exc:  # keep sweeping even if one attempt errors
                res = InitResult(address=addr, baud=baud, note=f"error: {exc}")
            if progress:
                progress(res)
            if res.keybytes is not None:
                found.append(res)
                # A module that woke up keeps its session open until it times
                # out (P3 max = 5 s); wait so it doesn't collide with the next probe.
                backend._sleep(5.5)
                break
            backend._sleep(W0_BUS_IDLE)
    return found


def parse_range(text: str) -> list[int]:
    """'0x10-0x20,0x33,0x40' -> list of ints."""
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x, 0) for x in part.split("-", 1))
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(part, 0))
    bad = [a for a in out if not 0 <= a <= 0xFF]
    if bad:
        raise ValueError(f"addresses must be 0x00-0xFF: {bad}")
    return out
