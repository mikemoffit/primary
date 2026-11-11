"""Generic OBD-II services (SAE J1979) on top of any backend.

A backend only needs `request(data: bytes) -> list[Response]`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import dtc as dtc_mod
from .frames import Response


class OBDError(Exception):
    pass


NEGATIVE_CODES = {
    0x10: "general reject",
    0x11: "service not supported",
    0x12: "sub-function not supported",
    0x21: "busy, repeat request",
    0x22: "conditions not correct",
    0x78: "response pending",
}


def _c_f(c: float) -> str:
    return f"{c:.0f} °C ({c * 9 / 5 + 32:.0f} °F)"


def _kmh(v: float) -> str:
    return f"{v:.0f} km/h ({v * 0.621371:.0f} mph)"


def _trim(a: int) -> float:
    return (a - 128) * 100 / 128


def _fuel_status(a: int) -> str:
    names = {1: "open loop (warming up)", 2: "closed loop",
             4: "open loop (load/decel)", 8: "open loop (fault)",
             16: "closed loop (O2 sensor fault)"}
    return names.get(a, f"0x{a:02X}")


def _o2(b: bytes) -> str:
    volts = b[0] / 200
    if b[1] == 0xFF:
        return f"{volts:.3f} V"
    return f"{volts:.3f} V, trim {_trim(b[1]):+.1f} %"


@dataclass(frozen=True)
class PID:
    pid: int
    name: str
    size: int
    fmt: Callable[[bytes], str]
    value: Callable[[bytes], float] | None = None  # numeric value for CSV logging


PIDS: dict[int, PID] = {p.pid: p for p in [
    PID(0x01, "Monitor status (see readiness)", 4, lambda b: b.hex(" ").upper()),
    PID(0x03, "Fuel system status", 2, lambda b: _fuel_status(b[0])),
    PID(0x04, "Engine load", 1, lambda b: f"{b[0] * 100 / 255:.1f} %", lambda b: b[0] * 100 / 255),
    PID(0x05, "Coolant temp", 1, lambda b: _c_f(b[0] - 40), lambda b: b[0] - 40),
    PID(0x06, "Short-term fuel trim bank 1", 1, lambda b: f"{_trim(b[0]):+.1f} %", lambda b: _trim(b[0])),
    PID(0x07, "Long-term fuel trim bank 1", 1, lambda b: f"{_trim(b[0]):+.1f} %", lambda b: _trim(b[0])),
    PID(0x08, "Short-term fuel trim bank 2", 1, lambda b: f"{_trim(b[0]):+.1f} %", lambda b: _trim(b[0])),
    PID(0x09, "Long-term fuel trim bank 2", 1, lambda b: f"{_trim(b[0]):+.1f} %", lambda b: _trim(b[0])),
    PID(0x0B, "Intake manifold pressure", 1, lambda b: f"{b[0]} kPa", lambda b: b[0]),
    PID(0x0C, "Engine RPM", 2, lambda b: f"{(b[0] * 256 + b[1]) / 4:.0f} rpm", lambda b: (b[0] * 256 + b[1]) / 4),
    PID(0x0D, "Vehicle speed", 1, lambda b: _kmh(b[0]), lambda b: b[0]),
    PID(0x0E, "Ignition timing advance", 1, lambda b: f"{b[0] / 2 - 64:.1f} ° BTDC", lambda b: b[0] / 2 - 64),
    PID(0x0F, "Intake air temp", 1, lambda b: _c_f(b[0] - 40), lambda b: b[0] - 40),
    PID(0x10, "Mass air flow", 2, lambda b: f"{(b[0] * 256 + b[1]) / 100:.2f} g/s", lambda b: (b[0] * 256 + b[1]) / 100),
    PID(0x11, "Throttle position", 1, lambda b: f"{b[0] * 100 / 255:.1f} %", lambda b: b[0] * 100 / 255),
    PID(0x13, "O2 sensors present", 1, lambda b: f"0b{b[0]:08b}"),
    *[PID(0x14 + i, f"O2 bank {1 + i // 4} sensor {1 + i % 4}", 2, _o2, lambda b: b[0] / 200)
      for i in range(8)],
    PID(0x1C, "OBD standard", 1, lambda b: {1: "OBD-II (CARB)", 2: "OBD (EPA)", 3: "OBD and OBD-II",
                                              6: "EOBD"}.get(b[0], f"0x{b[0]:02X}")),
    PID(0x1F, "Run time since start", 2, lambda b: f"{b[0] * 256 + b[1]} s", lambda b: b[0] * 256 + b[1]),
    PID(0x21, "Distance with MIL on", 2,
        lambda b: f"{b[0] * 256 + b[1]} km ({(b[0] * 256 + b[1]) * 0.621371:.0f} mi)",
        lambda b: b[0] * 256 + b[1]),
]}

DEFAULT_LIVE = [0x0C, 0x05, 0x0F, 0x04, 0x11, 0x10, 0x0E, 0x06, 0x07, 0x08, 0x09, 0x14, 0x18]

SPARK_MONITORS = ["Catalyst", "Heated catalyst", "Evaporative system", "Secondary air",
                  "A/C refrigerant", "O2 sensor", "O2 sensor heater", "EGR"]
CONTINUOUS_MONITORS = ["Misfire", "Fuel system", "Comprehensive components"]


@dataclass
class MonitorStatus:
    mil_on: bool
    dtc_count: int
    monitors: list[tuple[str, bool]]  # (name, complete) for supported monitors only


class OBD:
    def __init__(self, backend):
        self.backend = backend

    # ---- plumbing ------------------------------------------------------------
    def query(self, data: bytes, retries: int = 2) -> list[Response]:
        """Send a request and return the positive responses for that service."""
        want = data[0] + 0x40
        for _ in range(retries + 1):
            replies = self.backend.request(data)
            positive = [r for r in replies if r.mode == want]
            if positive:
                return positive
            neg = [r for r in replies if r.is_negative and len(r.data) >= 3 and r.data[1] == data[0]]
            if neg and neg[0].data[2] not in (0x21, 0x78):
                code = neg[0].data[2]
                raise OBDError(f"ECU refused service 0x{data[0]:02X}: "
                               f"{NEGATIVE_CODES.get(code, f'code 0x{code:02X}')}")
        return []

    # ---- mode 01 -------------------------------------------------------------
    def supported_pids(self) -> list[int]:
        supported: list[int] = []
        base = 0x00
        while base <= 0xC0:
            replies = self.query(bytes([0x01, base]))
            if not replies:
                break
            bitmap = 0
            for r in replies:
                if len(r.data) >= 6 and r.data[1] == base:
                    bitmap |= int.from_bytes(r.data[2:6], "big")
            for bit in range(32):
                if bitmap & (1 << (31 - bit)):
                    supported.append(base + bit + 1)
            if not bitmap & 1:  # PID base+0x20 (next range) not supported
                break
            base += 0x20
        return [p for p in supported if p % 0x20 != 0]

    def read_pid(self, pid: int) -> bytes | None:
        replies = self.query(bytes([0x01, pid]))
        for r in replies:
            if len(r.data) >= 2 and r.data[1] == pid:
                return r.data[2:]
        return None

    def monitor_status(self) -> MonitorStatus | None:
        data = self.read_pid(0x01)
        if not data or len(data) < 4:
            return None
        a, b, c, d = data[:4]
        monitors = []
        for i, name in enumerate(CONTINUOUS_MONITORS):
            if b & (1 << i):
                monitors.append((name, not b & (1 << (i + 4))))
        for i, name in enumerate(SPARK_MONITORS):
            if c & (1 << i):
                monitors.append((name, not d & (1 << i)))
        return MonitorStatus(mil_on=bool(a & 0x80), dtc_count=a & 0x7F, monitors=monitors)

    # ---- modes 03 / 07 / 04 ----------------------------------------------------
    def _dtcs(self, mode: int) -> list[str]:
        codes: list[str] = []
        for r in self.query(bytes([mode])):
            for code in dtc_mod.parse_dtc_payload(r.data[1:]):
                if code not in codes:
                    codes.append(code)
        return codes

    def stored_dtcs(self) -> list[str]:
        return self._dtcs(0x03)

    def pending_dtcs(self) -> list[str]:
        try:
            return self._dtcs(0x07)
        except OBDError:
            return []

    def clear_dtcs(self) -> bool:
        return bool(self.query(bytes([0x04])))

    # ---- mode 02 -------------------------------------------------------------
    def freeze_frame(self) -> dict[str, str]:
        """Snapshot the ECU stored when the first emissions fault set."""
        out: dict[str, str] = {}
        try:
            replies = self.query(bytes([0x02, 0x02, 0x00]), retries=0)
        except OBDError:
            return out
        if not replies or len(replies[0].data) < 5:
            return out
        trigger = dtc_mod.decode(replies[0].data[3], replies[0].data[4])
        if trigger == "P0000":
            return out
        out["Trigger code"] = trigger
        for pid in (0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0C, 0x0D, 0x0F, 0x11):
            try:
                replies = self.query(bytes([0x02, pid, 0x00]), retries=0)
            except OBDError:
                continue
            if replies and len(replies[0].data) >= 3 + PIDS[pid].size:
                out[PIDS[pid].name] = PIDS[pid].fmt(replies[0].data[3:])
        return out

    # ---- mode 09 -------------------------------------------------------------
    def vin(self) -> str | None:
        try:
            replies = self.query(bytes([0x09, 0x02]), retries=0)
        except OBDError:
            return None
        frames = sorted((r.data[2], r.data[3:]) for r in replies
                        if len(r.data) >= 4 and r.data[1] == 0x02)
        raw = b"".join(chunk for _, chunk in frames).lstrip(b"\x00")
        text = raw.decode("ascii", "replace")
        return text[-17:] if len(text) >= 17 else (text or None)


def format_pid(pid: int, data: bytes) -> tuple[str, str]:
    info = PIDS.get(pid)
    if info is None or len(data) < info.size:
        return (f"PID 0x{pid:02X}", data.hex(" ").upper())
    return (info.name, info.fmt(data))
