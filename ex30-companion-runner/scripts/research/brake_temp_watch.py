"""Live monitor for brake-pressure and battery-temperature candidates.

Two open hunts, one screen:

  BRAKE  — the reference scanner app shows four per-wheel brake pressures (FL/FR/RL/RR),
           but the 2026-07-16 snoop of that exact display shows the reference scanner app
           polls ONLY FD00/FD01/FD02 (ECU-E) for it — its 4th gauge is a
           duplicate. All three read the same value at steady state (median
           spread 0.17 bar during 0-62 bar braking; transient spread is
           polling lag). So per-wheel pressures, if exposed at all, are NOT
           in the reference scanner app traffic. This sweeps the whole FD00-FD0F family on
           ECU-E and decodes every response as u16 words (÷100 = bar).
           Pump the pedal and watch for:
             * four DIDs that ramp together with pedal force, OR
             * one DID whose payload holds four separate u16 words.
           FD00-FD02 are labelled (confirmed master pressure) for reference.

  TEMP   — Battery-temp DIDs on the BECM. Confirmed 2026-07-16 snoop
           (matched the reference scanner app gauges exactly):
             491B = average HV batt temp — u16/100 - 50 = °C
                    (0x1FD1=8145 → 31.45°C; NOT voltage as first guessed)
             4945 = max HV batt temp — byte0 = hottest sensor index,
                    bytes1-2 u16/100 - 50 = °C (0x11 2026 → #17, 32.30°C)
             4804 = coolant temp (confirmed 2026-07-15)
           Still hunting the min / per-module cell temps:
             489E, 4946, DD00, DA06 (u16 ÷100 candidates)
             DA90, DA91 (long arrays — likely per-module cell temps)
           Each row prints raw bytes + several decode interpretations so the
           exact scaling is visible against a reference-app reading.

Every row highlights yellow when its value changed since the last cycle, so
brake pressure is easy to catch on a pedal press.

Usage (on the Pi, over SSH):
  # dongle can only have one master — stop the display service first
  sudo systemctl stop driver-display
  python3 scripts/brake_temp_watch.py --focus brake     # pedal-press test
  python3 scripts/brake_temp_watch.py --focus temp      # slow temp drift
  python3 scripts/brake_temp_watch.py --focus all       # both (slower cycle)

Options:
  --port /dev/rfcomm0   serial port for the dongle (default)
  --focus brake|temp|all   which group to poll (default brake)
  --rate 0.5            seconds between cycles (brake wants fast, ~0.3-0.5)
  --scroll              append one line per cycle instead of a live table
                        (nicer if you want SSH scrollback / piping to a file)
  --once                single sweep then exit (smoke test)

Do NOT run the reference scanner app at the same time — the ELM327 has one shared AT state
and two masters clobber each other. Note the reference scanner app value FIRST, then run
this. Raw values are flushed to docs/brake_temp_<ts>.csv every cycle.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from config.settings import BluetoothConfig
from obd2.connection import OBD2Connection
from obd2.protocol import ELMProtocol
from obd2.pids import BECM, ECU_E, ECUContext


# ---------------------------------------------------------------------------
# Decoders — return a compact human string of candidate interpretations.
# ---------------------------------------------------------------------------

def _words(payload: str) -> list[int]:
    """Split payload into big-endian u16 words (drops a trailing odd byte)."""
    return [int(payload[i:i + 4], 16) for i in range(0, len(payload) - 3, 4)]


def decode_brake(payload: str) -> str:
    """Every u16 word as bar (÷100). If a DID packs 4 wheels, they show here."""
    ws = _words(payload)
    if not ws:
        return "—"
    return "bar=[" + ", ".join(f"{w / 100:.2f}" for w in ws) + "]"


def decode_temp(payload: str) -> str:
    """Byte list + the usual automotive temp/scale decodes."""
    n = len(payload) // 2
    if n == 0:
        return "—"
    b = [int(payload[i:i + 2], 16) for i in range(0, n * 2, 2)]
    parts = ["bytes=[" + ",".join(str(x) for x in b) + "]"]
    if n >= 2:
        u16 = (b[0] << 8) | b[1]
        parts.append(f"u16={u16}")
        parts.append(f"/100={u16 / 100:.2f}")
        parts.append(f"/10={u16 / 10:.1f}")
        # confirmed BECM temp scaling (491B): u16/100 - 50 = °C
        parts.append(f"/100-50={u16 / 100 - 50:.2f}")
        # second word (byte offset 1) — confirmed 4945 packs °C there
        u16b = (b[1] << 8) | b[2] if n >= 3 else None
        if u16b is not None:
            parts.append(f"@1/100={u16b / 100:.2f}")
            parts.append(f"@1/100-50={u16b / 100 - 50:.2f}")
    parts.append(f"b0-40={b[0] - 40}")
    parts.append(f"b0-50={b[0] - 50}")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Watch registry
# ---------------------------------------------------------------------------

@dataclass
class Row:
    ecu: ECUContext
    did: str
    label: str
    group: str                       # "brake" | "temp"
    decode: Callable[[str], str]
    confirmed: bool = False
    # runtime state
    payload: str = ""
    changed_ts: float = 0.0


def build_rows() -> list[Row]:
    rows: list[Row] = []

    # --- BRAKE: sweep FD00-FD0F on ECU-E -----------------------------------
    # FD03 confirmed as the 4th channel via live watch 2026-07-16.
    confirmed_master = {"FD00", "FD01", "FD02", "FD03"}
    for n in range(0x00, 0x10):
        did = f"FD{n:02X}"
        rows.append(Row(
            ecu=ECU_E, did=did, group="brake", decode=decode_brake,
            confirmed=did in confirmed_master,
            label=("master pressure (confirmed)" if did in confirmed_master
                   else "brake candidate"),
        ))

    # --- TEMP: BECM confirmed + candidates ---------------------------------
    temp_rows = [
        ("491B", "avg HV temp (confirmed) — /100-50=°C", True),
        ("4945", "max HV temp (confirmed) — b0=sensor#, @1/100-50=°C", True),
        ("4804", "coolant temp (confirmed)", True),
        ("489E", "avg-temp candidate (@1/100 ~29.3C)", False),
        ("4946", "inlet/coolant candidate (@1/100)", False),
        ("DD00", "temp candidate (u16/100)", False),
        ("DA06", "temp candidate (u16/100)", False),
        ("DD02", "old batt-temp guess (u8-40) — likely wrong", False),
        ("DA90", "cell array? (per-module temps)", False),
        ("DA91", "cell array? (per-module temps)", False),
    ]
    for did, label, confirmed in temp_rows:
        rows.append(Row(ecu=BECM, did=did, group="temp", decode=decode_temp,
                        confirmed=confirmed, label=label))

    return rows


def filter_rows(rows: list[Row], focus: str) -> list[Row]:
    if focus == "all":
        return rows
    return [r for r in rows if r.group == focus]


# ---------------------------------------------------------------------------
# Query helper (mirrors obd2/pids._extract_data multi-frame handling)
# ---------------------------------------------------------------------------

def query(protocol: ELMProtocol, did: str) -> str | None:
    raw, ok = protocol.query_raw_logged(f"22{did}")
    if not ok or not raw:
        return None
    clean = raw.replace(" ", "").replace("\r", "").replace("\n", "").upper()
    # strip ISO-TP frame indices like "0:", "1:"
    import re
    clean = re.sub(r"[0-9A-F]:", "", clean)
    marker = f"62{did.upper()}"
    idx = clean.find(marker)
    if idx == -1:
        return None
    tail = clean[idx + len(marker):]
    return "".join(c for c in tail if c in "0123456789ABCDEF")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _term_width() -> int:
    try:
        return shutil.get_terminal_size((120, 40)).columns
    except Exception:
        return 120


def render_table(rows: list[Row], cycle: int, cycle_ms: float,
                 focus: str, port: str, dead_ecus: set[str],
                 cycle_time_s: float) -> None:
    print("\033[H\033[J", end="")  # home + clear
    rule = "─" * min(_term_width(), 100)
    print(f"\033[1mEX30 brake/temp candidate watch\033[0m  focus={focus}  "
          f"cycle={cycle}  {cycle_ms:.0f}ms  port={port}")
    print("Yellow = changed this cycle.  Pump the brake to find 4 moving "
          "values; note the reference scanner app temp first to calibrate.")
    print(rule)
    last_ecu = None
    for r in rows:
        if r.ecu.name != last_ecu:
            tag = "  \033[1;31m[SWITCH FAILED]\033[0m" if r.ecu.name in dead_ecus else ""
            print(f"\n\033[1;36m{r.ecu.name}\033[0m{tag}")
            last_ecu = r.ecu.name
        mark = "\033[32m*\033[0m" if r.confirmed else " "
        recent = (time.monotonic() - r.changed_ts) < cycle_time_s * 1.5
        raw = r.payload or "—"
        raw_col = f"\033[1;33m{raw:<18}\033[0m" if recent else f"{raw:<18}"
        decoded = r.decode(r.payload) if r.payload else "—"
        print(f" {mark} {r.did}  {r.label:<38}  raw={raw_col} {decoded}")
    print(rule)
    print("Ctrl+C to stop. All rows flushed to CSV every cycle.")


def render_scroll(rows: list[Row], cycle: int) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    parts = [f"[{cycle:04d} {now}]"]
    for r in rows:
        if not r.payload:
            parts.append(f"{r.did}=--")
        elif r.group == "brake":
            ws = _words(r.payload)
            parts.append(f"{r.did}=" + "/".join(f"{w/100:.1f}" for w in ws) if ws else f"{r.did}=?")
        else:
            parts.append(f"{r.did}={r.payload}")
    print("  ".join(parts), flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="/dev/rfcomm0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--focus", choices=["brake", "temp", "all"], default="brake")
    p.add_argument("--rate", type=float, default=0.5,
                   help="Seconds between cycles (default 0.5; brake wants fast)")
    p.add_argument("--scroll", action="store_true",
                   help="One line per cycle instead of a live table")
    p.add_argument("--log", default=None, help="CSV path (default docs/brake_temp_<ts>.csv)")
    p.add_argument("--once", action="store_true", help="Single sweep then exit")
    args = p.parse_args()

    rows = filter_rows(build_rows(), args.focus)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = args.log or os.path.join(REPO_ROOT, "docs", f"brake_temp_{ts}.csv")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    bt = BluetoothConfig(port=args.port, baud_rate=args.baud)
    connection = OBD2Connection(bt)
    protocol = ELMProtocol(connection)

    csv_f = open(log_path, "w", newline="")
    writer = csv.writer(csv_f)
    writer.writerow(["timestamp", "ecu", "did", "group", "payload", "decoded"])

    try:
        connection.connect()
        if not protocol.initialize():
            print("ELM327 init failed — aborting", file=sys.stderr)
            return 1

        cycle = 0
        cycle_time_s = args.rate
        while True:
            cycle += 1
            t0 = time.monotonic()
            current_ecu: ECUContext | None = None
            dead_ecus: set[str] = set()

            for r in rows:
                if r.ecu.name in dead_ecus:
                    r.payload = ""
                    continue
                if r.ecu != current_ecu:
                    if not protocol.switch_ecu(r.ecu):
                        dead_ecus.add(r.ecu.name)
                        r.payload = ""
                        continue
                    current_ecu = r.ecu
                payload = query(protocol, r.did)
                if payload is not None and payload != r.payload:
                    r.payload = payload
                    r.changed_ts = time.monotonic()
                elif payload is None:
                    r.payload = ""
                decoded = r.decode(r.payload) if r.payload else ""
                writer.writerow([datetime.now().isoformat(timespec="milliseconds"),
                                 r.ecu.name, r.did, r.group, r.payload, decoded])

            csv_f.flush()
            cycle_time_s = max(time.monotonic() - t0, 0.05)
            if args.scroll:
                render_scroll(rows, cycle)
            else:
                render_table(rows, cycle, cycle_time_s * 1000, args.focus,
                             args.port, dead_ecus, cycle_time_s)

            if args.once:
                break
            sleep_for = args.rate - cycle_time_s
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        csv_f.close()
        connection.disconnect()
        print(f"Log written to {log_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
