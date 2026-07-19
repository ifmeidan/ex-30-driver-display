"""Battery-temp candidate watcher — drive-and-confirm phase of Research A.

The 2026-07-15 DID sweep (becm_did_sweep.py) found the likely battery
temps embedded mid-payload on the BECM:

    489E  00 0B74 57    u16@1 /100 = 29.32 C  <- prime avg-temp candidate
                        (the reference scanner app avg was 29.59 C, 12 min earlier)
    4946  00 0CEE 4B    u16@1 /100 = 33.10 C  (coolant/inlet temp?)
    DD00  0C62 07AF     u16@0 /100 = 31.70 C
    DA06  0A4F C540     u16@0 /100 = 26.39 C
    DA90/DA91           25-byte arrays, look like per-module cell temps

This polls that set every few seconds and logs to CSV. Run it while
DRIVING: real battery temps rise slowly and smoothly as the pack warms;
impostors jump around or stay flat. Compare the end values against a
fresh reference-app reading taken right after parking.

Usage:
  python scripts/temp_watch.py [--port /dev/rfcomm0] [--interval 5]
                               [--no-vcfront]

Battery temp does not need drive-READY, but the drive does. Start the
script, put the laptop down, drive normally, stop with Ctrl-C after.

Output: one console line per cycle + docs/temp_watch_<ts>.csv
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from config.settings import BluetoothConfig
from obd2.connection import OBD2Connection
from obd2.protocol import ELMProtocol
from obd2 import pids


def u16_at(payload: str, byte_off: int) -> int | None:
    h = payload[byte_off * 2: byte_off * 2 + 4]
    return int(h, 16) if len(h) == 4 else None


def dec_u16_scaled(byte_off: int, div: float):
    def f(payload: str) -> str:
        w = u16_at(payload, byte_off)
        return f"{w / div:.2f}C" if w is not None else "?"
    return f


def dec_u8_minus(byte_off: int, sub: int):
    def f(payload: str) -> str:
        h = payload[byte_off * 2: byte_off * 2 + 2]
        return f"{int(h, 16) - sub}C" if len(h) == 2 else "?"
    return f


def dec_raw(payload: str) -> str:
    return payload


# (ecu_name, did, label, decoder)
WATCHLIST = [
    ("BECM", "489E", "avg?u16@1/100", dec_u16_scaled(1, 100)),
    ("BECM", "4946", "cool?u16@1/100", dec_u16_scaled(1, 100)),
    ("BECM", "DD00", "u16@0/100", dec_u16_scaled(0, 100)),
    ("BECM", "DA06", "u16@0/100", dec_u16_scaled(0, 100)),
    ("BECM", "4804", "u8-50", dec_u8_minus(0, 50)),
    ("BECM", "4928", "(u16-400)/10", lambda p: (
        f"{(u16_at(p, 0) - 400) / 10:.1f}C" if u16_at(p, 0) is not None else "?")),
    ("BECM", "DD02", "old:u8-40", dec_u8_minus(0, 40)),
    ("BECM", "DA90", "array", dec_raw),
    ("BECM", "DA91", "array", dec_raw),
    ("VCFRONT", "4A33", "old:/100", dec_u16_scaled(2, 100)),
    ("VCFRONT", "4A35", "old:/100", dec_u16_scaled(2, 100)),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Battery temp candidate watch")
    parser.add_argument("--port", default="/dev/rfcomm0")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Seconds between cycles (default 5)")
    parser.add_argument("--no-vcfront", action="store_true",
                        help="Skip VCFRONT rows (no ECU switching)")
    args = parser.parse_args()

    watch = [w for w in WATCHLIST
             if not (args.no_vcfront and w[0] == "VCFRONT")]
    ecus = {"BECM": pids.BECM, "VCFRONT": pids.VCFRONT}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(REPO_ROOT, "docs", f"temp_watch_{timestamp}.csv")
    csv_f = open(csv_path, "w")
    csv_f.write("timestamp,ecu,did,payload,decoded\n")

    bt = BluetoothConfig(port=args.port)
    connection = OBD2Connection(bt)
    protocol = ELMProtocol(connection)

    try:
        connection.connect()
        if not protocol.initialize():
            print("!! ELM327 init failed - aborting.")
            return 1

        cycle = 0
        current_ecu = None
        while True:
            cycle += 1
            now = datetime.now().strftime("%H:%M:%S")
            parts = [f"[{cycle:03d} {now}]"]
            for ecu_name, did, label, decoder in watch:
                if ecu_name != current_ecu:
                    if not protocol.switch_ecu(ecus[ecu_name]):
                        parts.append(f"{ecu_name}:SWITCH-FAIL")
                        continue
                    current_ecu = ecu_name
                raw, _ = protocol.query_raw_logged(f"22{did}")
                clean = (raw or "").replace(" ", "").upper()
                marker = f"62{did}"
                if marker in clean:
                    payload = clean.split(marker, 1)[1]
                    payload = "".join(
                        c for c in payload if c in "0123456789ABCDEF")
                    decoded = decoder(payload)
                    parts.append(f"{did}={decoded}")
                    csv_f.write(f"{datetime.now().isoformat()},{ecu_name},"
                                f"{did},{payload},{decoded}\n")
                else:
                    parts.append(f"{did}=--")
                    csv_f.write(f"{datetime.now().isoformat()},{ecu_name},"
                                f"{did},,\n")
            csv_f.flush()
            print("  ".join(parts), flush=True)
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    finally:
        connection.disconnect()
        csv_f.close()
        print(f"CSV saved to: {csv_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
