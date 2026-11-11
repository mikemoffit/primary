"""ELM327 backend (generic OBD-II only).

An ELM327 handles the K-line wake-up and framing itself and only exposes
"send these OBD bytes".  That is enough for the engine ECU's emissions data,
but not for the module scanner or Land Rover-specific modules.  Use a KKL
cable for those.
"""

from __future__ import annotations

import time

from .frames import Response


class ELMError(Exception):
    pass


class ELM327Backend:
    name = "elm327"

    def __init__(self, port: str, baud: int = 38400, *, ser=None, log=None):
        self.port = port
        self.baud = baud
        self._ser = ser
        self._log = log
        self.init_result = None

    @property
    def ser(self):
        if self._ser is None:
            import serial

            self._ser = serial.Serial(self.port, self.baud, timeout=0.1)
        return self._ser

    def _cmd(self, cmd: str, timeout: float = 5.0) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode("ascii"))
        buf = b""
        deadline = time.monotonic() + timeout
        while b">" not in buf and time.monotonic() < deadline:
            buf += self.ser.read(256)
        text = buf.decode("ascii", "replace").replace(">", "").strip()
        if self._log:
            self._log(f"{cmd} -> {text!r}")
        return text

    def connect(self):
        ident = self._cmd("ATZ", timeout=3)
        for c in ("ATE0", "ATL0", "ATS1", "ATH0", "ATSP3"):  # SP3 = ISO 9141-2
            self._cmd(c)
        probe = self._cmd("0100", timeout=10)
        if "UNABLE" in probe or "ERROR" in probe or "NO DATA" in probe:
            raise ELMError(f"ELM327 could not talk to the engine ECU: {probe}")
        self.init_result = ident.splitlines()[-1] if ident else "ELM327"
        return self.init_result

    def request(self, data: bytes) -> list[Response]:
        text = self._cmd(data.hex(" ").upper())
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line or any(w in line for w in ("NO DATA", "SEARCHING", "STOPPED", "ERROR", "?")):
                continue
            try:
                payload = bytes.fromhex(line)
            except ValueError:
                continue
            out.append(Response(ecu=None, data=payload))
        return out

    def keepalive_due(self) -> bool:
        return False  # the ELM327 keeps the session alive itself

    def close(self):
        if self._ser is not None:
            self._ser.close()
            self._ser = None
