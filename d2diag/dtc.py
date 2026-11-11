"""Diagnostic trouble code decoding and descriptions."""

from __future__ import annotations

_LETTERS = "PCBU"

# Generic SAE J2012 descriptions for codes that commonly show up on Bosch
# Motronic V8s.  Codes not listed here are still decoded and shown; look the
# number up in a generic OBD-II code list or the Land Rover workshop manual.
DESCRIPTIONS = {
    "P0100": "Mass air flow circuit malfunction",
    "P0101": "Mass air flow circuit range/performance",
    "P0102": "Mass air flow circuit low input",
    "P0103": "Mass air flow circuit high input",
    "P0110": "Intake air temperature circuit malfunction",
    "P0115": "Engine coolant temperature circuit malfunction",
    "P0116": "Engine coolant temperature circuit range/performance",
    "P0120": "Throttle position sensor circuit malfunction",
    "P0125": "Insufficient coolant temperature for closed-loop fuel control",
    "P0130": "O2 sensor circuit malfunction (bank 1 sensor 1)",
    "P0133": "O2 sensor slow response (bank 1 sensor 1)",
    "P0135": "O2 sensor heater circuit malfunction (bank 1 sensor 1)",
    "P0136": "O2 sensor circuit malfunction (bank 1 sensor 2)",
    "P0141": "O2 sensor heater circuit malfunction (bank 1 sensor 2)",
    "P0150": "O2 sensor circuit malfunction (bank 2 sensor 1)",
    "P0153": "O2 sensor slow response (bank 2 sensor 1)",
    "P0155": "O2 sensor heater circuit malfunction (bank 2 sensor 1)",
    "P0156": "O2 sensor circuit malfunction (bank 2 sensor 2)",
    "P0161": "O2 sensor heater circuit malfunction (bank 2 sensor 2)",
    "P0171": "System too lean (bank 1)",
    "P0172": "System too rich (bank 1)",
    "P0174": "System too lean (bank 2)",
    "P0175": "System too rich (bank 2)",
    "P0300": "Random/multiple cylinder misfire detected",
    "P0301": "Cylinder 1 misfire detected",
    "P0302": "Cylinder 2 misfire detected",
    "P0303": "Cylinder 3 misfire detected",
    "P0304": "Cylinder 4 misfire detected",
    "P0305": "Cylinder 5 misfire detected",
    "P0306": "Cylinder 6 misfire detected",
    "P0307": "Cylinder 7 misfire detected",
    "P0308": "Cylinder 8 misfire detected",
    "P0325": "Knock sensor 1 circuit malfunction (bank 1)",
    "P0330": "Knock sensor 2 circuit malfunction (bank 2)",
    "P0335": "Crankshaft position sensor circuit malfunction",
    "P0340": "Camshaft position sensor circuit malfunction",
    "P0400": "EGR flow malfunction",
    "P0420": "Catalyst efficiency below threshold (bank 1)",
    "P0430": "Catalyst efficiency below threshold (bank 2)",
    "P0440": "Evaporative emission system malfunction",
    "P0441": "Evaporative emission system incorrect purge flow",
    "P0442": "Evaporative emission system leak detected (small leak)",
    "P0443": "Evaporative emission purge control valve circuit",
    "P0455": "Evaporative emission system leak detected (large leak)",
    "P0500": "Vehicle speed sensor malfunction",
    "P0505": "Idle control system malfunction",
    "P0560": "System voltage malfunction",
    "P0603": "Internal control module memory checksum error",
    "P0700": "Transmission control system malfunction (read the gearbox ECU)",
}


def decode(hi: int, lo: int) -> str:
    """Convert the two raw DTC bytes into the familiar 'P0171' form."""
    letter = _LETTERS[(hi >> 6) & 0x03]
    return f"{letter}{(hi >> 4) & 0x03}{hi & 0x0F:X}{lo:02X}"


def describe(code: str) -> str:
    if code in DESCRIPTIONS:
        return DESCRIPTIONS[code]
    if len(code) == 5 and code[1] in "13":
        return "manufacturer-specific code (see Land Rover documentation)"
    return "no description in local table"


def parse_dtc_payload(payload: bytes) -> list[str]:
    """Parse pairs of DTC bytes, skipping 00 00 padding."""
    codes = []
    for i in range(0, len(payload) - 1, 2):
        hi, lo = payload[i], payload[i + 1]
        if hi == 0 and lo == 0:
            continue
        codes.append(decode(hi, lo))
    return codes
