"""A pretend Motronic engine ECU, for trying the tool without the truck.

Also provides FakeKLine, a byte-level serial-port fake used by the tests to
exercise the real KKL code path (5-baud init, echo, framing).
"""

from __future__ import annotations

import math
import time

from .frames import Response, build_response, checksum, REQUEST_HEADER

ECU_ADDR = 0x10


class SimECU:
    """Answers OBD requests with plausible warm-idle V8 values."""

    def __init__(self, dtcs=((0x01, 0x71), (0x01, 0x74)), pending=((0x03, 0x04),)):
        self.dtcs = list(dtcs)
        self.pending = list(pending)
        self.t0 = time.monotonic()

    def _pid(self, pid: int) -> bytes | None:
        t = time.monotonic() - self.t0
        rpm = 680 + 25 * math.sin(t * 1.3)
        o2 = int(90 + 80 * (0.5 + 0.5 * math.sin(t * 6)))
        table = {
            0x01: bytes([0x80 | len(self.dtcs), 0x07, 0x65, 0x04]),
            0x03: bytes([0x02, 0x02]),
            0x04: bytes([52]),
            0x05: bytes([128]),
            0x06: bytes([130]), 0x07: bytes([150]),
            0x08: bytes([127]), 0x09: bytes([152]),
            0x0B: bytes([35]),
            0x0C: int(rpm * 4).to_bytes(2, "big"),
            0x0D: bytes([0]),
            0x0E: bytes([140]),
            0x0F: bytes([70]),
            0x10: (480).to_bytes(2, "big"),
            0x11: bytes([30]),
            0x13: bytes([0x33]),
            0x14: bytes([o2, 0xFF]), 0x15: bytes([0x60, 0xFF]),
            0x18: bytes([200 - o2, 0xFF]), 0x19: bytes([0x62, 0xFF]),
            0x1C: bytes([1]),
        }
        if pid == 0x00:  # supported-PID bitmap for 0x01-0x20, built from the table
            bitmap = sum(1 << (32 - p) for p in table if 1 <= p <= 0x20)
            return bitmap.to_bytes(4, "big")
        return table.get(pid)

    @staticmethod
    def _dtc_frames(mode: int, codes) -> list[bytes]:
        flat = [b for c in codes for b in c] or [0, 0]
        frames = []
        for i in range(0, max(len(flat), 1), 6):
            chunk = flat[i:i + 6]
            frames.append(bytes([mode]) + bytes(chunk + [0] * (6 - len(chunk))))
        return frames

    def handle(self, data: bytes) -> list[bytes]:
        mode = data[0]
        if mode == 0x01 and len(data) >= 2:
            val = self._pid(data[1])
            return [bytes([0x41, data[1]]) + val] if val is not None else []
        if mode == 0x02 and len(data) >= 3:
            if data[1] == 0x02:
                code = self.dtcs[0] if self.dtcs else (0, 0)
                return [bytes([0x42, 0x02, 0x00, *code])]
            val = self._pid(data[1])
            return [bytes([0x42, data[1], 0x00]) + val] if val is not None else []
        if mode == 0x03:
            return self._dtc_frames(0x43, self.dtcs)
        if mode == 0x07:
            return self._dtc_frames(0x47, self.pending)
        if mode == 0x04:
            self.dtcs.clear()
            self.pending.clear()
            return [bytes([0x44])]
        if mode == 0x09:
            return [bytes([0x7F, 0x09, 0x11])]
        return [bytes([0x7F, mode, 0x11])]


class SimBackend:
    name = "sim"

    def __init__(self, ecu: SimECU | None = None):
        self.ecu = ecu or SimECU()
        self.init_result = "simulated Motronic ECU (no hardware)"

    def connect(self):
        return self.init_result

    def request(self, data: bytes) -> list[Response]:
        time.sleep(0.02)
        return [Response(ECU_ADDR, d) for d in self.ecu.handle(data)]

    def keepalive_due(self) -> bool:
        return False

    def close(self):
        pass


class FakeKLine:
    """Byte-level stand-in for a KKL cable plugged into a truck.

    Reconstructs the 5-baud address byte from break_condition toggles,
    replies with sync/keybytes, echoes every written byte like a real
    K-line cable, and answers framed requests via SimECU.
    """

    def __init__(self, responders: dict[int, tuple[int, int]] | None = None, echo=True, ecu=None):
        self.responders = responders if responders is not None else {0x33: (0x08, 0x08)}
        self.echo = echo
        self.ecu = ecu or SimECU()
        self.baudrate = 10400
        self.rx = bytearray()
        self._bits: list[int] = []
        self._break = False
        self._awaiting_inv: int | None = None
        self._addr: int | None = None
        self._req = bytearray()
        self.closed = False

    # pyserial-compatible surface
    @property
    def break_condition(self):
        return self._break

    @break_condition.setter
    def break_condition(self, value):
        # Every setter call is one bit period: idle(1), start(0), 8 data bits,
        # stop(1), then a final release(1) -- 12 calls in total.
        self._break = bool(value)
        self._bits.append(0 if self._break else 1)
        if not self._break:
            self._maybe_decode_address()

    def _maybe_decode_address(self):
        bits = self._bits
        if len(bits) >= 12 and bits[0] == 1 and bits[1] == 0:
            data_bits = bits[2:10]
            addr = sum(b << i for i, b in enumerate(data_bits))
            self._bits = []
            if addr in self.responders and self.baudrate == 10400:
                kb1, kb2 = self.responders[addr]
                self.rx += bytes([0x00, 0x55, kb1, kb2])  # leading junk from break
                self._awaiting_inv = (~kb2) & 0xFF
                self._addr = addr

    def reset_input_buffer(self):
        self.rx.clear()

    def read(self, n=1):
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def write(self, data: bytes):
        for b in data:
            if self.echo:
                self.rx.append(b)
            if self._awaiting_inv is not None:
                if b == self._awaiting_inv:
                    self.rx.append((~self._addr) & 0xFF)
                self._awaiting_inv = None
                continue
            self._req.append(b)
            self._try_answer()
        return len(data)

    def _try_answer(self):
        req = self._req
        if len(req) >= 5 and bytes(req[:3]) == REQUEST_HEADER and checksum(req[:-1]) == req[-1]:
            payload = bytes(req[3:-1])
            self._req = bytearray()
            for frame in self.ecu.handle(payload):
                self.rx += build_response(ECU_ADDR, frame)

    def close(self):
        self.closed = True
