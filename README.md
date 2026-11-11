# d2diag: Discovery II V8 diagnostics from a laptop

A small command-line tool that reads a 2003 Land Rover Discovery II V8 through
a USB OBD cable: engine fault codes, freeze frames, readiness monitors and
live data. It also has a scanner that finds the truck's other modules (ABS,
SLS, ACE, BCU and so on) so they can be added next.

> **Status: stage 1.** Engine diagnostics use the standard ISO 9141-2 protocol
> that the Bosch Motronic engine ECU supports. Nothing here has run against a
> real truck yet. It is tested against a byte-level simulator. Expect to send
> back output from the first real run so problems can be fixed.

## What to buy

Get a **"VAG-COM KKL 409.1" USB cable with a genuine FTDI FT232RL chip**
(about $15–25; the listing should say FTDI). It's a plain USB-to-K-line
adapter, so this software controls all the timing. That control is what makes
reaching the non-engine modules possible.

Older **"VAG-COM 704" / HEX-USB** cables (hardware ID `VID_0403&PID_FA20`) are
the same FTDI-plus-K-line design under a different USB ID, so they should work
too, once the operating system exposes them as a serial port:

- **Windows:** if Device Manager lists it under *Universal Serial Bus
  controllers* rather than *Ports (COM & LPT)*, open its Properties, go to the
  *Advanced* tab, tick **Load VCP**, then unplug and replug it. If there's no
  Advanced tab, install FTDI's VCP driver and point it at the device.
- **Linux:** `echo 0403 fa20 | sudo tee /sys/bus/usb-serial/drivers/ftdi_sio/new_id`
  (after `sudo modprobe ftdi_sio`), and it appears as `/dev/ttyUSB0`.

An **ELM327** cable also works with `--backend elm327`, but only for the
generic engine commands (`info`, `dtc`, `clear`, `live`). It can't run `scan`
or talk to the other modules.

## Install

Needs Python 3.9 or newer on Windows, macOS or Linux.

```
pip install pyserial
git clone <this repo> && cd primary
python -m d2diag --help
```

Try it with no truck attached, using the built-in simulator:

```
python -m d2diag dtc  -b sim
python -m d2diag live -b sim
```

## Using it on the truck

1. Plug the cable into the OBD port under the dash and into the laptop.
2. Ignition **ON**. The engine can be off or running; for `clear` and `scan`
   it should be off.
3. Find the serial port:

   ```
   python -m d2diag ports
   ```

   It shows something like `COM3` on Windows, `/dev/ttyUSB0` on Linux, or
   `/dev/tty.usbserial-XXXX` on a Mac.

| Command | What it does |
|---|---|
| `python -m d2diag info -p COM3` | Supported live-data items, check-engine light, readiness monitors |
| `python -m d2diag dtc -p COM3` | Stored and pending engine codes, plus the freeze frame |
| `python -m d2diag live -p COM3` | Live RPM, temperatures, fuel trims, O2 sensors, MAF... (Ctrl+C to stop) |
| `python -m d2diag live -p COM3 --log run.csv` | The same, also saved to a spreadsheet file |
| `python -m d2diag live -p COM3 --pids 0C,06,07,08,09` | Choose which items to show (hex IDs, listed by `info`) |
| `python -m d2diag clear -p COM3` | Clear engine codes (asks first; resets readiness monitors) |
| `python -m d2diag scan -p COM3` | Find other modules on the diagnostic line (see below) |
| `python -m d2diag raw -p COM3 "01 0C"` | Send a raw OBD request (for debugging) |

Add `-v` to any command to see the raw bytes going over the wire. That output
is the most useful thing to send back if something doesn't work.

### Useful things to watch on a V8

- **Long-term fuel trims (`07`, `09`)** sitting well above +10 % on both
  banks, often with P0171/P0174, usually mean an intake (vacuum) leak or a
  weak MAF sensor. That's a common Discovery V8 issue.
- **O2 sensor voltages (`14`, `18`)** should swing quickly between about 0.1 V
  and 0.9 V at warm idle. A sensor that sits still or moves slowly points to a
  failing sensor.

## Module scan (stage 2 groundwork)

The ABS, self-levelling suspension, ACE, body control unit, instrument pack,
airbag, HEVAC and gearbox modules share the diagnostic line but don't answer
generic OBD requests, and their addresses aren't well documented. `scan`
sends the standard 5-baud wake-up to each address and records which ones
answer and how:

```
python -m d2diag scan -p COM3                      # full sweep, up to ~20 min
python -m d2diag scan -p COM3 --range 0x00-0x7F    # part of the range
```

It sends only wake-up handshakes, never commands, so it can't change
anything. Send the list it prints back, and the next stage will add fault
reading for those modules.

## How it works

- `d2diag/kline.py`: the raw K-line driver. It bit-banges the 5-baud
  wake-up by toggling the UART break, handles the cable's echo, and
  respects ISO 9141-2 timing.
- `d2diag/frames.py`: ISO 9141-2 message framing and checksums.
- `d2diag/obd.py`: generic OBD-II services and value decoding.
- `d2diag/elm327.py`: the alternative ELM327 backend.
- `d2diag/sim.py`: a simulated engine ECU and a byte-level fake cable.
- `d2diag/scan.py`: the module discovery sweep.

Run the tests with `pip install pytest && python -m pytest`.

## Safety

Everything except `clear` only reads. Keep your Nanocom as the tool of record,
especially for anything that writes to a module, until this tool has proven
itself on your truck.
