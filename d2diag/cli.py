"""Command-line interface: python -m d2diag <command> [options]"""

from __future__ import annotations

import argparse
import csv
import sys
import time

from . import dtc as dtc_mod
from .obd import DEFAULT_LIVE, OBD, OBDError, PIDS, format_pid


def make_backend(args):
    log = (lambda m: print(f"  [{m}]", file=sys.stderr)) if args.verbose else None
    if args.backend == "sim":
        from .sim import SimBackend

        return SimBackend()
    if not args.port:
        sys.exit("error: --port is required (e.g. COM3, /dev/ttyUSB0, /dev/tty.usbserial-XXXX). "
                 "Run 'python -m d2diag ports' to list serial ports.")
    if args.backend == "elm327":
        from .elm327 import ELM327Backend

        return ELM327Backend(args.port, log=log)
    from .kline import KLineBackend

    return KLineBackend(args.port, log=log)


def connect(args):
    backend = make_backend(args)
    print(f"Connecting via {backend.name} ...", flush=True)
    try:
        res = backend.connect()
    except Exception as exc:
        backend.close()
        sys.exit(f"error: {exc}")
    desc = res.describe() if hasattr(res, "describe") else str(res)
    print(f"Connected: {desc}\n")
    return backend, OBD(backend)


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


# ---- commands -----------------------------------------------------------------

def cmd_ports(args):
    try:
        from serial.tools import list_ports
    except ImportError:
        sys.exit("pyserial is not installed: pip install pyserial")
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.  Is the cable plugged in and its driver installed?")
    for p in ports:
        print(f"{p.device:24} {p.description}  [{p.hwid}]")
        hwid = (p.hwid or "").upper()
        if "0403:6001" in hwid:
            print(f"{'':24} ^ FTDI FT232R -- likely your KKL cable")
        elif "0403:FA20" in hwid:
            print(f"{'':24} ^ FTDI with Ross-Tech HEX-USB ID (VAG-COM 704 style) -- a K-line cable")


def cmd_info(args):
    backend, obd = connect(args)
    try:
        vin = obd.vin()
        print(f"VIN: {vin or '(not reported -- normal for many pre-2005 ECUs)'}")
        pids = obd.supported_pids()
        print(f"Supported live-data PIDs ({len(pids)}):")
        for p in pids:
            name = PIDS[p].name if p in PIDS else "(not decoded by this tool yet)"
            print(f"  0x{p:02X}  {name}")
        _print_monitors(obd)
    finally:
        backend.close()


def _print_monitors(obd):
    st = obd.monitor_status()
    if st is None:
        return
    print(f"\nCheck-engine light: {'ON' if st.mil_on else 'off'}   stored emissions codes: {st.dtc_count}")
    print("Readiness monitors (needed to pass an emissions test):")
    for name, done in st.monitors:
        print(f"  {name:28} {'complete' if done else 'NOT complete'}")


def cmd_dtc(args):
    backend, obd = connect(args)
    try:
        stored = obd.stored_dtcs()
        pending = obd.pending_dtcs()
        print("Stored codes:" if stored else "Stored codes: none")
        for c in stored:
            print(f"  {c}  {dtc_mod.describe(c)}")
        print("Pending codes (seen once, not yet confirmed):" if pending else "Pending codes: none")
        for c in pending:
            print(f"  {c}  {dtc_mod.describe(c)}")
        ff = obd.freeze_frame()
        if ff:
            print("\nFreeze frame (conditions when the first code set):")
            for k, v in ff.items():
                print(f"  {k:30} {v}")
        _print_monitors(obd)
    except OBDError as exc:
        sys.exit(f"error: {exc}")
    finally:
        backend.close()


def cmd_clear(args):
    print("This clears the engine's stored fault codes and freeze-frame data, and\n"
          "RESETS ALL READINESS MONITORS -- the truck will fail an emissions test until\n"
          "it has been driven enough to complete them again (often 50-100+ miles).\n"
          "Ignition ON, engine OFF.")
    if not confirm("Clear codes?", args.yes):
        print("Cancelled.")
        return
    backend, obd = connect(args)
    try:
        ok = obd.clear_dtcs()
        print("Codes cleared." if ok else "The ECU did not confirm the clear (is the engine running?).")
    except OBDError as exc:
        sys.exit(f"error: {exc}")
    finally:
        backend.close()


def cmd_live(args):
    backend, obd = connect(args)
    try:
        if args.pids:
            pids = [int(p, 16) for p in args.pids.split(",")]
        else:
            supported = set(obd.supported_pids())
            pids = [p for p in DEFAULT_LIVE if p in supported] or DEFAULT_LIVE
        writer = None
        fh = None
        if args.log:
            fh = open(args.log, "w", newline="")
            writer = csv.writer(fh)
            writer.writerow(["time_s"] + [PIDS[p].name if p in PIDS else f"0x{p:02X}" for p in pids])
        t0 = time.monotonic()
        print("Live data -- Ctrl+C to stop\n")
        while True:
            rows, values = [], []
            for p in pids:
                data = obd.read_pid(p)
                if data is None:
                    rows.append((PIDS[p].name if p in PIDS else f"PID 0x{p:02X}", "--"))
                    values.append("")
                    continue
                rows.append(format_pid(p, data))
                info = PIDS.get(p)
                values.append(f"{info.value(data):.3f}" if info and info.value and len(data) >= info.size
                              else data.hex())
            if writer:
                writer.writerow([f"{time.monotonic() - t0:.2f}"] + values)
            if sys.stdout.isatty():
                print("\x1b[H\x1b[J", end="")
            print(f"t = {time.monotonic() - t0:6.1f} s")
            for name, val in rows:
                print(f"  {name:32} {val}")
            print(flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if args.log and fh:
            fh.close()
            print(f"Logged to {args.log}")
        backend.close()


def cmd_scan(args):
    if args.backend != "kkl":
        sys.exit("error: scanning needs raw K-line access, i.e. --backend kkl with a KKL cable.")
    from .kline import KLineBackend
    from .scan import parse_range, scan

    addresses = parse_range(args.range)
    bauds = [int(b) for b in args.bauds.split(",")]
    est = len(addresses) * len(bauds) * 2.4 / 60
    print(f"Probing {len(addresses)} addresses at {bauds} baud (~{est:.0f} min worst case).\n"
          "Ignition ON, engine OFF.  This only sends wake-up handshakes, no commands.\n")
    log = (lambda m: print(f"  [{m}]", file=sys.stderr)) if args.verbose else None
    backend = KLineBackend(args.port, log=log)

    def progress(res):
        if res.keybytes is not None:
            print(f"  FOUND  {res.describe()}")
        elif args.verbose:
            print(f"  ....   {res.describe()}")
        else:
            print(f"\r  probing 0x{res.address:02X} @ {res.baud} ...", end="", flush=True)

    try:
        found = scan(backend, addresses, bauds, progress)
    except KeyboardInterrupt:
        found = None
        print("\nStopped.")
    finally:
        backend.close()
    if found is not None:
        print(f"\n\n{len(found)} module(s) answered:")
        for r in found:
            print(f"  {r.describe()}   raw: {r.raw.hex(' ').upper()}")
        print("\nSend this list back so the next stage can target those modules.")


def cmd_raw(args):
    backend, obd = connect(args)
    try:
        data = bytes.fromhex(args.hex)
        for r in backend.request(data):
            ecu = f"0x{r.ecu:02X}" if r.ecu is not None else "?"
            print(f"ECU {ecu}: {r.data.hex(' ').upper()}")
    finally:
        backend.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="d2diag", description="Land Rover Discovery II V8 diagnostics over a USB K-line cable")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", "-p", help="serial port of the cable (COM3, /dev/ttyUSB0, ...)")
    common.add_argument("--backend", "-b", choices=["kkl", "elm327", "sim"], default="kkl",
                        help="kkl = dumb FTDI K-line cable (default), elm327, or sim (no hardware)")
    common.add_argument("--verbose", "-v", action="store_true", help="show raw bytes")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("ports", help="list serial ports").set_defaults(func=cmd_ports)
    sub.add_parser("info", parents=[common], help="VIN, supported PIDs, readiness").set_defaults(func=cmd_info)
    sub.add_parser("dtc", parents=[common], help="read engine fault codes").set_defaults(func=cmd_dtc)
    p = sub.add_parser("clear", parents=[common], help="clear engine fault codes")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    p.set_defaults(func=cmd_clear)
    p = sub.add_parser("live", parents=[common], help="live engine data")
    p.add_argument("--pids", help="comma-separated hex PIDs, e.g. 0C,05,06,07")
    p.add_argument("--interval", type=float, default=0.2, help="seconds between refreshes")
    p.add_argument("--log", help="also write values to this CSV file")
    p.add_argument("--once", action="store_true", help="print one sample and exit")
    p.set_defaults(func=cmd_live)
    p = sub.add_parser("scan", parents=[common], help="discover modules on the K-line")
    p.add_argument("--range", default="0x00-0xFF", help="addresses, e.g. 0x00-0x7F,0xA0")
    p.add_argument("--bauds", default="10400,9600", help="baud rates to listen at")
    p.set_defaults(func=cmd_scan)
    p = sub.add_parser("raw", parents=[common], help="send a raw OBD request, e.g. '01 0C'")
    p.add_argument("hex")
    p.set_defaults(func=cmd_raw)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
