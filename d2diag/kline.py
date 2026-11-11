"""Raw K-line access through a "dumb" USB-to-K-line cable (VAG-COM KKL / FTDI).

The cable is a plain serial port wired to OBD pin 7 (K-line).  Everything
else -- the 5-baud wake-up, echo handling, frame timing -- is done here in
software, which is exactly why this kind of cable can reach modules that an
ELM327 cannot.

Protocol reference: ISO 9141-2 (the variant used by the Bosch Motronic 5.2.1
engine ECU on the Discovery II V8 for its OBD-II emissions diagnostics).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .frames import Response, build_request, split_responses

# ---- ISO 9141-2 timing (seconds) -------------------------------------------
W0_BUS_IDLE = 0.300   # bus must be idle this long before the address byte (W5)
BIT_5BAUD = 0.200     # one bit at 5 baud
W1_MAX = 0.300        # address byte -> sync byte
W4 = 0.030            # keybyte 2 -> inverted keybyte 2 (25..50 ms)
P3_MIN = 0.060        # end of ECU response -> next tester request (55 ms min)
P4 = 0.006            # tester inter-byte gap (5..20 ms)
RESPONSE_SILENCE = 0.080  # gap that ends a burst of ECU responses (P2 max 50 ms)
FIRST_BYTE_TIMEOUT = 0.400
KEEPALIVE_INTERVAL = 2.0  # session dies after P3 max = 5 s of silence

OBD_ADDRESS = 0x33    # ISO 9141-2 functional OBD address
DEFAULT_BAUD = 10400


class KLineError(Exception):
    pass


@dataclass
class InitResult:
    address: int
    baud: int
    sync: int | None = None
    keybytes: tuple[int, int] | None = None
    inverted_address: int | None = None
    raw: bytes = b""
    ok: bool = False
    note: str = ""
    extra: dict = field(default_factory=dict)

    def describe(self) -> str:
        if self.keybytes is None:
            return f"0x{self.address:02X} @ {self.baud}: no response"
        kb = f"KB1=0x{self.keybytes[0]:02X} KB2=0x{self.keybytes[1]:02X}"
        status = "OK" if self.ok else "partial"
        return f"0x{self.address:02X} @ {self.baud}: {status} sync=0x{self.sync:02X} {kb} {self.note}".rstrip()


def _open_serial(port: str, baud: int):
    import serial  # imported lazily so the simulator works without pyserial

    return serial.Serial(port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                         timeout=0.01, write_timeout=1)


class KLineBackend:
    """ISO 9141-2 session over a dumb K-line cable."""

    name = "kkl"

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, *, ser=None,
                 sleep=time.sleep, clock=time.monotonic, log=None):
        self.port = port
        self.baud = baud
        self._ser = ser
        self._sleep = sleep
        self._clock = clock
        self._log = log
        self.echo = True          # most KKL cables echo what we send; auto-detected
        self._last_rx = 0.0
        self.init_result: InitResult | None = None

    # ---- low level -----------------------------------------------------------
    @property
    def ser(self):
        if self._ser is None:
            self._ser = _open_serial(self.port, self.baud)
        return self._ser

    def _trace(self, direction: str, data: bytes):
        if self._log and data:
            self._log(f"{direction} {data.hex(' ').upper()}")

    def _read_until_silence(self, first_timeout: float, silence: float) -> bytes:
        """Read bytes until `silence` seconds pass without a new byte."""
        buf = bytearray()
        start = self._clock()
        last = None
        while True:
            chunk = self.ser.read(64)
            now = self._clock()
            if chunk:
                buf += chunk
                last = now
            elif last is None:
                if now - start > first_timeout:
                    break
            elif now - last > silence:
                break
        if buf:
            self._last_rx = self._clock()
        return bytes(buf)

    def _write(self, data: bytes):
        """Write byte-by-byte with P4 spacing and swallow the cable's echo."""
        for b in data:
            self.ser.write(bytes([b]))
            if self.echo:
                echoed = self._read_exact(1, 0.1)
                if echoed != bytes([b]):
                    raise KLineError(
                        f"echo mismatch: sent {b:02X}, got {echoed.hex() or 'nothing'} "
                        "(bus collision or no ignition?)")
            self._sleep(P4)
        self._trace("TX", data)

    def _read_exact(self, n: int, timeout: float) -> bytes:
        buf = bytearray()
        deadline = self._clock() + timeout
        while len(buf) < n and self._clock() < deadline:
            buf += self.ser.read(n - len(buf))
        return bytes(buf)

    # ---- 5-baud slow init ------------------------------------------------------
    def _send_5baud(self, address: int):
        """Bit-bang one byte at 5 baud by toggling the UART break condition.

        Break on = K-line pulled low (logic 0).  Frame: start bit (0),
        8 data bits LSB first, stop bit (1).
        """
        ser = self.ser
        ser.break_condition = False
        self._sleep(W0_BUS_IDLE)
        bits = [0] + [(address >> i) & 1 for i in range(8)] + [1]
        for bit in bits:
            ser.break_condition = (bit == 0)
            self._sleep(BIT_5BAUD)
        ser.break_condition = False

    def slow_init(self, address: int = OBD_ADDRESS, baud: int | None = None) -> InitResult:
        """Wake a module with the ISO 9141 5-baud init and complete the handshake."""
        baud = baud or self.baud
        ser = self.ser
        if ser.baudrate != baud:
            ser.baudrate = baud
        ser.reset_input_buffer()
        self._send_5baud(address)

        result = InitResult(address=address, baud=baud)
        # Toggling break can leave junk (0x00 / framing errors) in the buffer,
        # so scan for the 0x55 sync byte rather than trusting the first byte.
        raw = bytearray()
        deadline = self._clock() + W1_MAX + 0.5
        while self._clock() < deadline:
            raw += ser.read(16)
            idx = raw.find(0x55)
            if idx >= 0 and len(raw) >= idx + 3:
                break
        result.raw = bytes(raw)
        idx = raw.find(0x55)
        if idx < 0 or len(raw) < idx + 3:
            result.note = "no sync" if not raw else f"unrecognised reply {raw.hex(' ')}"
            return result

        result.sync = 0x55
        kb1, kb2 = raw[idx + 1], raw[idx + 2]
        result.keybytes = (kb1, kb2)

        # Tester answers with inverted KB2; module answers with inverted address.
        self._sleep(W4)
        inv_kb2 = (~kb2) & 0xFF
        ser.write(bytes([inv_kb2]))
        reply = self._read_exact(1, 0.25)
        if reply == bytes([inv_kb2]):
            # The cable echoed our byte back; remember that for later writes.
            self.echo = True
            reply = self._read_exact(1, 0.1)
        else:
            self.echo = False
        if reply[:1] == bytes([(~address) & 0xFF]):
            result.inverted_address = reply[0]
            result.ok = True
        else:
            result.note = f"no inverted-address ack (got {reply.hex() or 'nothing'})"
        if kb1 == kb2 and kb1 in (0x08, 0x94):
            result.note = (result.note + " ISO 9141-2").strip()
        elif kb2 == 0x8F:
            result.note = (result.note + " KWP2000 keybytes").strip()
        self._last_rx = self._clock()
        return result

    # ---- session API (shared with the other backends) --------------------------
    def connect(self) -> InitResult:
        last = None
        for attempt in range(3):
            res = self.slow_init(OBD_ADDRESS)
            if res.ok:
                self.init_result = res
                return res
            last = res
            self._sleep(W0_BUS_IDLE + 2.5 * attempt)
        raise KLineError(
            "engine ECU did not answer the 5-baud init: "
            f"{last.note if last else 'no reply'}.  Check: ignition ON, cable in, "
            "correct serial port, genuine FTDI-based KKL cable.")

    def request(self, data: bytes) -> list[Response]:
        wait = P3_MIN - (self._clock() - self._last_rx)
        if wait > 0:
            self._sleep(wait)
        self.ser.reset_input_buffer()
        self._write(build_request(data))
        raw = self._read_until_silence(FIRST_BYTE_TIMEOUT, RESPONSE_SILENCE)
        self._trace("RX", raw)
        return split_responses(raw)

    def keepalive_due(self) -> bool:
        return self._clock() - self._last_rx > KEEPALIVE_INTERVAL

    def close(self):
        if self._ser is not None:
            self._ser.close()
            self._ser = None
