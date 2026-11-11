import pytest

from d2diag import dtc
from d2diag.frames import build_request, build_response, checksum, split_responses
from d2diag.kline import KLineBackend, KLineError
from d2diag.obd import OBD, format_pid
from d2diag.scan import parse_range, scan
from d2diag.sim import FakeKLine, SimBackend, SimECU


class FakeClock:
    """Deterministic time so the 5-baud init doesn't take 2 real seconds."""

    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        self.t += 0.001  # each read "costs" a millisecond
        return self.t


def kline(fake):
    c = FakeClock()
    return KLineBackend("FAKE", ser=fake, sleep=c.sleep, clock=c.now)


# ---- framing ------------------------------------------------------------------

def test_request_frame_matches_spec():
    # 01 00 request, checksum = (68+6A+F1+01+00) & FF = C4
    assert build_request(b"\x01\x00") == bytes.fromhex("686AF10100C4")


def test_request_length_limits():
    with pytest.raises(ValueError):
        build_request(b"")
    with pytest.raises(ValueError):
        build_request(bytes(8))


def test_split_multiple_frames_with_noise():
    a = build_response(0x10, bytes.fromhex("410C0AA0"))
    b = build_response(0x18, bytes.fromhex("43017100000000"))
    rs = split_responses(b"\x00\xff" + a + b)
    assert [(r.ecu, r.data.hex()) for r in rs] == [(0x10, "410c0aa0"), (0x18, "43017100000000")]


def test_split_rejects_bad_checksum():
    frame = bytearray(build_response(0x10, b"\x41\x05\x80"))
    frame[-1] ^= 0xFF
    assert split_responses(bytes(frame)) == []


def test_checksum():
    assert checksum(bytes([0xFF, 0x02])) == 0x01


# ---- DTC decoding -------------------------------------------------------------

@pytest.mark.parametrize("hi,lo,code", [
    (0x01, 0x71, "P0171"), (0x03, 0x00, "P0300"), (0x14, 0x23, "P1423"),
    (0x41, 0x23, "C0123"), (0x81, 0x00, "B0100"), (0xC0, 0x01, "U0001"),
])
def test_dtc_decode(hi, lo, code):
    assert dtc.decode(hi, lo) == code


def test_dtc_payload_skips_padding():
    assert dtc.parse_dtc_payload(bytes.fromhex("017101740000")) == ["P0171", "P0174"]


# ---- OBD services against the simulator ---------------------------------------

def test_services_on_sim():
    obd = OBD(SimBackend(SimECU()))
    assert obd.stored_dtcs() == ["P0171", "P0174"]
    assert obd.pending_dtcs() == ["P0304"]
    assert 0x0C in obd.supported_pids()
    st = obd.monitor_status()
    assert st.mil_on and st.dtc_count == 2
    assert obd.freeze_frame()["Trigger code"] == "P0171"
    assert obd.vin() is None
    assert obd.clear_dtcs()
    assert obd.stored_dtcs() == []


def test_many_dtcs_span_frames():
    codes = [(0x03, i) for i in range(1, 9)]
    obd = OBD(SimBackend(SimECU(dtcs=codes)))
    assert obd.stored_dtcs() == [f"P030{i}" for i in range(1, 9)]


def test_pid_formulas():
    assert format_pid(0x0C, bytes([0x0A, 0xA0])) == ("Engine RPM", "680 rpm")
    assert format_pid(0x05, bytes([128]))[1] == "88 °C (190 °F)"
    assert format_pid(0x06, bytes([128]))[1] == "+0.0 %"
    assert format_pid(0x14, bytes([90, 0xFF]))[1] == "0.450 V"


# ---- the real KKL code path, byte by byte -------------------------------------

@pytest.mark.parametrize("echo", [True, False])
def test_kline_slow_init_and_requests(echo):
    fake = FakeKLine(echo=echo)
    be = kline(fake)
    res = be.connect()
    assert res.ok and res.keybytes == (0x08, 0x08) and res.inverted_address == 0xCC
    assert be.echo is echo
    obd = OBD(be)
    assert obd.stored_dtcs() == ["P0171", "P0174"]
    rpm = obd.read_pid(0x0C)
    assert rpm is not None and 600 < (rpm[0] * 256 + rpm[1]) / 4 < 750


def test_5baud_bit_order():
    """The address byte must go out LSB first between start and stop bits."""
    fake = FakeKLine(responders={0xA5: (0x94, 0x94)})
    be = kline(fake)
    res = be.slow_init(0xA5)
    assert res.ok and res.keybytes == (0x94, 0x94)


def test_connect_fails_cleanly_with_no_ecu():
    be = kline(FakeKLine(responders={}))
    with pytest.raises(KLineError, match="ignition ON"):
        be.connect()


def test_scan_finds_modules():
    fake = FakeKLine(responders={0x33: (0x08, 0x08), 0x40: (0x01, 0x8F)})
    be = kline(fake)
    found = scan(be, range(0x30, 0x42), bauds=[10400])
    assert [r.address for r in found] == [0x33, 0x40]


def test_parse_range():
    assert parse_range("0x10-0x12,0x33") == [0x10, 0x11, 0x12, 0x33]
    with pytest.raises(ValueError):
        parse_range("0x100")
