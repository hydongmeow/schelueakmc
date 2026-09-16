#!/usr/bin/env python3
"""Record a capture from the A53 SMP experiment console and analyse it.

    python capture.py COM5 --seconds 120 --out captures/run1.log

Opens the CP2105 "Standard COM Port" (MAIN_UART0, micro-USB J11) at 921600,
survives the port disappearing across a board power-cycle (the CP2105 sits on
a board rail), waits for the firmware banner, records for --seconds after it,
then runs latency.py on the result.  The SBL's own 115200-baud output that
precedes the banner is unreadable at 921600 and is discarded.

With --wait-for-banner the recorder keeps waiting (up to --timeout seconds)
for a banner, so it can be started before the board is powered on: the
banner is printed exactly once at boot and latency.py refuses to work without
it, so the recorder must already be listening when the board comes up.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import serial  # pyserial

BAUD = 921600
BANNER = b"=== SK-AM64 A53 SMP FreeRTOS Scheduler Latency Test ==="


def open_port(port: str, deadline: float) -> serial.Serial:
    """Open the port, retrying until the deadline (it vanishes on power-cycle)."""
    while True:
        try:
            return serial.Serial(port, BAUD, timeout=0.5)
        except serial.SerialException:
            if time.time() > deadline:
                raise
            time.sleep(0.5)


def record(port: str, seconds: float, timeout: float) -> bytes:
    deadline = time.time() + timeout
    buf = b""
    handle = open_port(port, deadline)
    print(f"listening on {port} at {BAUD} for the banner "
          f"(up to {timeout:.0f} s) ...", flush=True)

    # Phase 1: wait for the banner.
    while BANNER not in buf:
        if time.time() > deadline:
            sys.exit("ERROR: no banner seen; is the board powered, in OSPI boot "
                     "mode with the experiment flashed, and is this the "
                     "Standard COM Port of the CP2105?")
        try:
            buf += handle.read(65536)
        except serial.SerialException:
            handle.close()
            handle = open_port(port, deadline)
        buf = buf[-4096:] if BANNER not in buf else buf[buf.index(BANNER):]

    print("banner seen, recording ...", flush=True)
    # Phase 2: record for the requested time.
    end = time.time() + seconds
    while time.time() < end:
        try:
            buf += handle.read(65536)
        except serial.SerialException:
            sys.exit("ERROR: port lost during the capture (power-cycled?); "
                     "the capture is incomplete, run again")
    handle.close()
    return buf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("port", help="serial port, e.g. COM5 or /dev/ttyUSB1")
    parser.add_argument("--seconds", type=float, default=120.0,
                        help="recording time after the banner (default 120)")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="how long to wait for the banner (default 300 s)")
    parser.add_argument("--out", type=Path, default=None,
                        help="log path (default captures/<timestamp>.log)")
    parser.add_argument("--no-report", action="store_true",
                        help="do not run latency.py afterwards")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    out = args.out or here / "captures" / time.strftime("%Y-%m-%d_%H%M%S.log")
    out.parent.mkdir(parents=True, exist_ok=True)

    data = record(args.port, args.seconds, args.timeout)
    out.write_bytes(data)
    lines = data.count(b"\n")
    print(f"wrote {out} ({len(data)} bytes, {lines} lines)")

    if not args.no_report:
        report = out.with_suffix(".report.txt")
        result = subprocess.run([sys.executable, str(here / "latency.py"), str(out)],
                                capture_output=True, text=True, errors="replace")
        report.write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout)
        print(f"report written to {report}")


if __name__ == "__main__":
    main()
