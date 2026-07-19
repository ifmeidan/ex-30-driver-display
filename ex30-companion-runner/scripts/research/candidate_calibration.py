"""Live calibration monitor for unconfirmed DID candidates.

Polls open-question DID candidates and prints raw bytes side-by-side with
several plausible decode interpretations every cycle, so the operator can
match a row against a known reference (dashboard, the reference scanner app reading noted
beforehand) and derive the scaling formula.

Open questions this is built to answer:
  - Battery temperature: RESOLVED 2026-07-16 (the reference scanner app snoop, exact match):
      491B (BECM) = average HV temp — u16/100 - 50 = °C (8145 → 31.45°C)
      4945 (BECM) = max HV temp — byte0 = hottest sensor index,
                    bytes1-2 u16/100 - 50 = °C (11 2026 → sensor 17, 32.30°C)
    Both kept below as confirmed reference rows to validate live readings.
    DD02's old raw-40 guess is superseded. Still open: min / per-module
    cell temps (4907/4908 triplets, VCFRONT 4A28-4A34 u16/10 sensors).
  - Brake pressure: FD00/FD01/FD02 (ECU-E) confirmed as master pressure,
    u16/100 = bar (2026-07-15 ramp + 2026-07-16 snoop 0-62 bar). The
    2026-07-16 snoop shows the reference scanner app's four wheel gauges poll ONLY these
    three DIDs — per-wheel pressures are not in the reference scanner app traffic. If they
    exist they live on the ABS module — find its address first with
    scripts/abs_module_scan.py.

Usage:
  python scripts/candidate_calibration.py [--port /dev/rfcomm0]
                                          [--focus temp|brake|all]
                                          [--rate 1.0]
                                          [--log docs/calibration_<ts>.csv]
                                          [--ecu-d-extended]
                                          [--once]

In the car:
  1. If the Pi's display service owns the dongle, stop it first:
       sudo systemctl stop driver-display
     (Two readers on /dev/rfcomm0 garble each other.)
     On macOS pass --port /dev/cu.<dongle-name> instead.
  2. Do NOT run the reference scanner app at the same time — the ELM327 has one shared
     AT-command state; two masters clobber each other's headers/filters.
     Instead: note the reference scanner app gauge value FIRST (e.g. battery temp),
     disconnect the reference scanner app, then run this script and compare. Temps drift
     slowly, so a sequential comparison is valid.
  3. Sit in READY mode (foot on brake, button pressed) so BECM/VCFRONT are
     awake. For --focus brake also select D and engage the parking brake —
     ECU-D/E/F return NO DATA outside drive-READY.
  4. --focus temp: watch the BECM/VCFRONT rows, compare the °C hints
     against your noted reference-app/dashboard battery temp.
     --focus brake: hold the pedal at idle → light → hard for ~5 s each
     and watch for a row that moves monotonically with pedal force.
  5. Press Ctrl+C when done. Raw values are appended to CSV continuously
     (flushed every cycle), so a crash or BT drop loses nothing.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from config.settings import BluetoothConfig
from obd2.connection import OBD2Connection
from obd2.protocol import ELMProtocol
from obd2.pids import (
    BECM, VCFRONT, ECU_D, ECU_E, ECU_F,
    ECUContext,
)


# ---------------------------------------------------------------------------
# Raw decoding — show the bytes as ints so the operator can compare against
# a known reference and derive the formula themselves. Temp-group rows also
# get °C hints for the common automotive formulas (raw-40, raw-50, u16/10).
# ---------------------------------------------------------------------------

def decode_raw(payload: str) -> list[tuple[str, str]]:
    """Return a length-appropriate set of (label, value) interpretations."""
    out: list[tuple[str, str]] = []
    n = len(payload) // 2  # number of bytes
    if n == 0:
        return out

    bytes_list = [int(payload[i:i+2], 16) for i in range(0, n*2, 2)]

    if n == 1:
        out.append(("u8", str(bytes_list[0])))
    else:
        out.append(("bytes", "[" + ",".join(str(b) for b in bytes_list) + "]"))

    if n >= 2:
        u16 = (bytes_list[0] << 8) | bytes_list[1]
        s16 = u16 - 0x10000 if u16 & 0x8000 else u16
        out.append(("u16", str(u16)))
        out.append(("s16", str(s16)))

    if n >= 3:
        u24 = (bytes_list[0] << 16) | (bytes_list[1] << 8) | bytes_list[2]
        out.append(("u24", str(u24)))

    if n >= 4:
        u32 = (bytes_list[0] << 24) | (bytes_list[1] << 16) | (bytes_list[2] << 8) | bytes_list[3]
        s32 = u32 - 0x100000000 if u32 & 0x80000000 else u32
        out.append(("u32", str(u32)))
        if s32 != u32:
            out.append(("s32", str(s32)))
        # u16 pairs for chassis-state-style multi-channel responses
        pairs = []
        for i in range(0, (n // 2) * 2, 2):
            pairs.append((bytes_list[i] << 8) | bytes_list[i+1])
        out.append(("u16_pairs", "[" + ",".join(str(v) for v in pairs) + "]"))

    return out


def temp_hints(payload: str) -> list[str]:
    """°C interpretations via the usual automotive formulas, filtered to a
    plausible HV-battery window so noise doesn't clutter the display."""
    hints: list[str] = []
    n = len(payload) // 2
    if n == 0:
        return hints
    b0 = int(payload[:2], 16)
    for label, v in (("-40", b0 - 40), ("-50", b0 - 50), ("/2-40", b0 / 2 - 40)):
        if -30 <= v <= 70:
            hints.append(f"u8{label}={v:g}°C?")
    if n >= 2:
        u16 = (b0 << 8) | int(payload[2:4], 16)
        v = u16 / 10
        if -30 <= v <= 90:
            hints.append(f"u16/10={v:.1f}°C?")
        # confirmed BECM scaling (491B/4945): u16/100 - 50 = °C
        v = u16 / 100 - 50
        if -30 <= v <= 90:
            hints.append(f"u16/100-50={v:.2f}°C?")
    if n >= 3:
        # 4945 layout: byte0 = sensor index, bytes1-2 = u16/100 - 50 °C
        v = int(payload[2:6], 16) / 100 - 50
        if -30 <= v <= 90:
            hints.append(f"@1/100-50={v:.2f}°C?")
    if n == 4:
        # VCFRONT 413A/4A2x pack the value in the LAST two bytes
        # (leading bytes are zero padding): 00000163 -> 355 -> 35.5°C.
        v = int(payload[4:8], 16) / 10
        if -30 <= v <= 90:
            hints.append(f"tail-u16/10={v:.1f}°C?")
    if n == 3:
        per_byte = [int(payload[i:i+2], 16) - 40 for i in range(0, 6, 2)]
        if all(-30 <= v <= 70 for v in per_byte):
            hints.append("bytes-40=[" + ",".join(f"{v:g}" for v in per_byte) + "]°C?")
    return hints


# ---------------------------------------------------------------------------
# Candidate registry
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    ecu: ECUContext
    did: str
    label: str
    hypothesis: str
    source: str
    group: str = "misc"          # temp | brake | misc
    # Filled at runtime
    last_payload: str = ""
    last_change_ts: float = 0.0


# NOTE: keep this list ordered by ECU (BECM → VCFRONT → ECU-F → ECU-E →
# ECU-D) so the poll loop batches context switches. Filtering by --focus
# preserves that order.
CANDIDATES: list[Candidate] = [
    # ---------------- BECM battery temp (29-bit) — focus: temp ----------------
    Candidate(
        ecu=BECM, did="491B", group="temp",
        label="491B avg HV temp",
        hypothesis="CONFIRMED avg HV batt temp — u16/100 - 50 = °C; sanity row",
        source="snoop 2026-07-16: 0x1FD1=8145 → 31.45°C, exact reference-app match",
    ),
    Candidate(
        ecu=BECM, did="4945", group="temp",
        label="4945 max HV temp",
        hypothesis="CONFIRMED max HV batt temp — b0=sensor#, bytes1-2 /100-50=°C",
        source="snoop 2026-07-16: 11 2026 → sensor 17, 32.30°C, reference-app match",
    ),
    Candidate(
        ecu=BECM, did="DD02", group="temp",
        label="DD02 batt temp",
        hypothesis="Superseded by 491B/4945 — raw-40 guess never confirmed",
        source="snoop 2026-04-04: raw=0x3C (60); demoted 2026-07-16",
    ),
    Candidate(
        ecu=BECM, did="4907", group="temp",
        label="4907 3-byte",
        hypothesis="Cell temp min/max/avg (3 separate bytes)",
        source="snoop 2026-04-04: 30 10 10 at rest",
    ),
    Candidate(
        ecu=BECM, did="4908", group="temp",
        label="4908 3-byte",
        hypothesis="Cell voltage min/max/avg OR coolant temp triplet",
        source="snoop 2026-04-04: 43 10 07 at rest",
    ),
    Candidate(
        ecu=BECM, did="DA17", group="temp",
        label="DA17 3-byte",
        hypothesis="Unknown triplet — cheap to watch alongside 4907/4908",
        source="snoop 2026-04-04: 30 05 10 at rest",
    ),
    Candidate(
        ecu=BECM, did="4801", group="temp",
        label="4801 HV pack V",
        hypothesis="HV pack voltage (CONFIRMED u16/100 = V) — pipeline sanity check",
        source="CONFIRMED — expect raw ~36000-44000 (÷100 = 360-440 V)",
    ),

    # ---------------- VCFRONT temp sensors (29-bit) — focus: temp ----------------
    Candidate(
        ecu=VCFRONT, did="413A", group="temp",
        label="413A temp",
        hypothesis="Coolant/battery-inlet temp (u16/10 = 40.7°C in snoop)",
        source="snoop 2026-04-04: 0x0197 (407)",
    ),
] + [
    Candidate(
        ecu=VCFRONT, did=did, group="temp",
        label=f"{did} temp{i+1}",
        hypothesis="Temp sensor bank (last-u16/10 = °C guess) — one may be HV batt",
        source="snoop 2026-04-04: 40.7-62.0°C range if ÷10",
    )
    # Exact snoop set — NOT contiguous (4A2A-4A2F never responded).
    # becm_temp_scan sweeps the full 4A20-4A3F for completeness.
    for i, did in enumerate(["4A28", "4A29", "4A30", "4A31", "4A32", "4A33", "4A34"])
] + [
    # ---------------- ECU-F aggregator (29-bit) — focus: brake ----------------
    # E30x are live and unlabeled; the aggregator is lead #1 for brake
    # position/pressure.
    Candidate(
        ecu=ECU_F, did=did, group="brake",
        label=f"{did} ECU-F aggr",
        hypothesis=hyp,
        source=src,
    )
    for did, hyp, src in [
        ("E300", "Unlabeled, ~1741 — brake/gear/pedal candidate",
         "snoop 2026-04-05: 06CD/06D0, active"),
        ("E301", "Unlabeled, ~8188", "snoop 2026-04-05: 1FFC, active"),
        ("E303", "0x4000 midpoint — centered signal (pedal? steering?)",
         "snoop 2026-04-05: 4000"),
        ("E304", "Unlabeled, ~8188 (same as E301?)", "snoop 2026-04-05: 1FFC"),
        ("E306", "SoC candidate (0x43=67)", "snoop 2026-04-05: 43"),
        ("E312", "0x4000 midpoint — centered signal", "snoop 2026-04-05: 4000"),
    ]
] + [
    Candidate(
        ecu=ECU_F, did="EE9A", group="misc",
        label="EE9A (ECU-F)",
        hypothesis="Outside temp OR 12V battery temp (1 byte)",
        source="snoop 2026-04-04: raw=0x3A (58) at rest",
    ),

    # ---------------- ECU-E motor (29-bit) — focus: brake ----------------
    Candidate(
        ecu=ECU_E, did="FD00", group="brake",
        label="FD00 ERAD",
        hypothesis="Pedal-position ratio OR motor RPM (snoop values look ratio-like)",
        source="snoop 2026-04-04: 0x001E-0x0034 at rest, cycled FAST",
    ),
    Candidate(
        ecu=ECU_E, did="FD01", group="brake",
        label="FD01 ERAD",
        hypothesis="Pedal ratio OR motor torque (signed16, expect ± in regen)",
        source="snoop 2026-04-04: 0x001E-0x0034 at rest, cycled FAST",
    ),
    Candidate(
        ecu=ECU_E, did="FD02", group="brake",
        label="FD02 ERAD",
        hypothesis="Pedal ratio OR motor/inverter temp",
        source="snoop 2026-04-04: 0x0021-0x0034 at rest, cycled FAST",
    ),
    Candidate(
        ecu=ECU_E, did="F40D", group="brake",
        label="F40D speed",
        hypothesis="Vehicle speed (CONFIRMED km/h) — expect 0 while parked",
        source="CONFIRMED drive test 2026-04-05: tracks dash ±1 km/h",
    ),
    Candidate(
        ecu=ECU_E, did="2B06", group="misc",
        label="2B06 wheel FL",
        hypothesis="Wheel speed FL (CONFIRMED km/h) — expect 0 while parked",
        source="CONFIRMED drive test 2026-04-05 (NOT brake pressure)",
    ),
    Candidate(
        ecu=ECU_E, did="2B11", group="misc",
        label="2B11 signed16",
        hypothesis="Yaw rate / lateral accel / outside temp÷2 (signed16, near 0)",
        source="snoop 2026-04-04: 0xFFF3-0xFFFA at rest (signed -13..-6)",
    ),
    Candidate(
        ecu=ECU_E, did="FEE7", group="misc",
        label="FEE7 multi-byte",
        hypothesis="Wheel-speed array or chassis state (11 bytes in snoop)",
        source="snoop 2026-04-04: 00A000A000A000A000A000 (11 bytes)",
    ),

    # ---------------- ECU-D thermal (29-bit) — focus: brake ----------------
    # EE19/EE1A/EE1B were all-zero / coolant temps in the DEFAULT session.
    # Re-check under extended diagnostic session with --ecu-d-extended
    #.
    Candidate(
        ecu=ECU_D, did="EE19", group="brake",
        label="EE19 (ECU-D)",
        hypothesis="All-zero in default session — may wake under session 1003",
        source="drive test 2026-04-05: zero under braking; ruled out UNLESS 1003 changes it",
    ),
    Candidate(
        ecu=ECU_D, did="EE1A", group="brake",
        label="EE1A (ECU-D)",
        hypothesis="2 coolant loop temps (NOT brake temps) — watch for session change",
        source="drive test 2026-04-05: [17,15,17,15,0,0,0,0] pattern",
    ),
    Candidate(
        ecu=ECU_D, did="EE1B", group="brake",
        label="EE1B (ECU-D)",
        hypothesis="All-zero in default session — may wake under session 1003",
        source="snoop 2026-04-04: 000000",
    ),
    Candidate(
        ecu=ECU_D, did="EE06", group="misc",
        label="EE06 (ECU-D)",
        hypothesis="Cabin or outside temp from HVAC ECU (1 byte)",
        source="snoop 2026-04-04: 0xD7-0xFA varying — possibly cabin sensor",
    ),
]


def filter_candidates(focus: str) -> list[Candidate]:
    if focus == "all":
        return CANDIDATES
    return [c for c in CANDIDATES if c.group == focus]


# ---------------------------------------------------------------------------
# Query + decode helpers (mirroring obd2/pids.py:_extract_data so we stay
# compatible with the project's ATH0 init).
# ---------------------------------------------------------------------------

_MULTIFRAME_PREFIX = re.compile(r"[0-9A-F]:")


def extract_payload(raw: str, did: str) -> str | None:
    if not raw:
        return None
    clean = _MULTIFRAME_PREFIX.sub("", raw.upper())
    clean = clean.replace(" ", "").replace("\r", "").replace("\n", "")
    marker = f"62{did.upper()}"
    idx = clean.find(marker)
    if idx == -1:
        return None
    return clean[idx + len(marker):]


def query_did(protocol: ELMProtocol, did: str) -> tuple[str, str | None]:
    """Returns (raw_response, payload_after_header_or_None)."""
    raw, ok = protocol.query_raw_logged(f"22{did}")
    if not ok:
        return (raw or "", None)
    return (raw, extract_payload(raw, did))


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def term_width() -> int:
    try:
        return shutil.get_terminal_size((120, 40)).columns
    except Exception:
        return 120


def colorize_changed(value: str, changed: bool) -> str:
    return f"\033[1;33m{value}\033[0m" if changed else value


def clear_screen() -> None:
    print("\033[H\033[J", end="")


def render(candidates: list[Candidate], cycle: int, cycle_time_s: float,
           port: str, focus: str, failed_ecus: set[str]) -> None:
    clear_screen()
    width = term_width()
    rule = "─" * min(width, 110)

    print(f"\033[1mEX30 Candidate PID Calibration\033[0m   focus={focus}  "
          f"cycle={cycle}  cycle_time={cycle_time_s*1000:.0f}ms  port={port}")
    print("Compare each row against your noted reference-app/dashboard value. "
          "Highlighted = changed since last cycle.")
    print(rule)

    last_ecu: ECUContext | None = None
    for c in candidates:
        if c.ecu != last_ecu:
            ecu_label = (f"{c.ecu.name}  header={c.ecu.header} "
                         f"rx={c.ecu.rx_filter}  ATSP{c.ecu.protocol}")
            if c.ecu.name in failed_ecus:
                ecu_label += "  \033[1;31m[SWITCH FAILED — skipped this cycle]\033[0m"
            print(f"\n\033[1;36m{ecu_label}\033[0m")
            last_ecu = c.ecu

        payload = c.last_payload or ""
        if payload:
            decoded_parts = [f"{name}={value}" for name, value in decode_raw(payload)]
            if c.group == "temp":
                decoded_parts += temp_hints(payload)
            decoded_str = "  ".join(decoded_parts)
        else:
            decoded_str = "—"

        changed = (time.monotonic() - c.last_change_ts) < cycle_time_s * 1.5
        raw_str = colorize_changed(f"raw={payload or '—':<12}", changed)
        print(f"  {c.did}  {c.label:<22}  {raw_str}  {decoded_str}")
        print(f"        ↳ {c.hypothesis}")
        print(f"        source: {c.source}")

    print(rule)
    print("Press Ctrl+C to stop. Raw values are flushed to CSV every cycle.")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="/dev/rfcomm0",
                   help="Serial port for OBD dongle (macOS: /dev/cu.<dongle-name>)")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate")
    p.add_argument("--focus", choices=["temp", "brake", "all"], default="all",
                   help="Which candidate group to poll. Smaller group = faster "
                        "cycle = better chance of catching a pedal press.")
    p.add_argument("--rate", type=float, default=1.0,
                   help="Seconds between full cycles (default 1.0)")
    p.add_argument("--log", default=None,
                   help="CSV log path (default: docs/calibration_<ts>.csv)")
    p.add_argument("--ecu-d-extended", action="store_true",
                   help="Send UDS 1003 (extended diagnostic session) after each "
                        "switch to ECU-D, in case EE19/EE1B only publish there. "
                        "Read-only; the session auto-expires within seconds.")
    p.add_argument("--once", action="store_true",
                   help="Single sweep then exit (smoke test)")
    args = p.parse_args()

    candidates = filter_candidates(args.focus)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = args.log or os.path.join(
        REPO_ROOT, "docs", f"calibration_{timestamp}.csv")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    bt = BluetoothConfig(port=args.port, baud_rate=args.baud)
    connection = OBD2Connection(bt)
    protocol = ELMProtocol(connection)

    csv_f = open(log_path, "w", newline="")
    writer = csv.writer(csv_f)
    writer.writerow(["timestamp", "ecu", "did", "group", "raw_response", "payload"])

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

            # Group by ECU to minimise context switches. A failed switch
            # marks the whole ECU dead for this cycle — retrying per
            # candidate would burn seconds in recovery each time.
            current_ecu: ECUContext | None = None
            failed_ecus: set[str] = set()
            for c in candidates:
                if c.ecu.name in failed_ecus:
                    c.last_payload = ""
                    continue
                if c.ecu != current_ecu:
                    if not protocol.switch_ecu(c.ecu):
                        failed_ecus.add(c.ecu.name)
                        c.last_payload = ""
                        continue
                    current_ecu = c.ecu
                    if args.ecu_d_extended and c.ecu == ECU_D:
                        raw, _ = protocol.query_raw_logged("1003")
                        ts = datetime.now().isoformat(timespec="milliseconds")
                        writer.writerow([ts, c.ecu.name, "1003(session)",
                                         c.group, raw, ""])

                raw, payload = query_did(protocol, c.did)
                ts = datetime.now().isoformat(timespec="milliseconds")
                writer.writerow([ts, c.ecu.name, c.did, c.group, raw, payload or ""])
                if payload is not None and payload != c.last_payload:
                    c.last_payload = payload
                    c.last_change_ts = time.monotonic()

            csv_f.flush()
            cycle_time_s = max(time.monotonic() - t0, 0.05)
            render(candidates, cycle, cycle_time_s, args.port, args.focus,
                   failed_ecus)

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
