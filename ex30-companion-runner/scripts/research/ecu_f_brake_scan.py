"""Brake-pedal toggle sweep — Research B.1 + B.2.

Sweeps E300-E30F on ECU-F (the brake / drivetrain aggregator) and FD00-FD02
on ECU-E (the motor ECU — its values look more like pedal-position ratios
than rpm in the BT snoop) across three brake states: idle / light / hard.

Any DID that moves monotonically with brake force is a brake-pressure or
brake-position candidate. Run with the car in READY, gear in D, parking
brake engaged or foot on brake to keep it stationary.

Usage:
  python scripts/ecu_f_brake_scan.py [--port /dev/rfcomm0]

Output: prints a side-by-side table, saves the full log to
docs/scan_results_ecu_f_brake_<timestamp>.log
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

# Make the repo root importable when run from scripts/.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from config.settings import BluetoothConfig
from obd2.connection import OBD2Connection
from obd2.protocol import ELMProtocol
from obd2.pids import ECU_F, ECU_E


ECU_F_DIDS = [f"E30{i:X}" for i in range(0x0, 0x10)]   # E300 .. E30F
ECU_E_DIDS = ["FD00", "FD01", "FD02"]

STATES = ["idle", "light", "hard"]


_log_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    _log_lines.append(msg)


def save_log(path: str) -> None:
    with open(path, "w") as f:
        f.write("\n".join(_log_lines) + "\n")
    print(f"\nLog saved to: {path}")


def extract_payload(raw: str, did: str) -> str | None:
    """Strip ELM noise, return hex payload after 62<DID> marker."""
    if not raw:
        return None
    clean = raw.replace(" ", "").replace("\r", "").replace("\n", "").upper()
    # Strip multi-frame sequence indicators (e.g. "0:", "1:")
    import re
    clean = re.sub(r"[0-9A-F]:", "", clean)
    marker = f"62{did.upper()}"
    idx = clean.find(marker)
    if idx == -1:
        return None
    return clean[idx + len(marker):]


def snapshot(protocol: ELMProtocol, ecu, dids: list[str]) -> dict[str, str | None]:
    """Query every DID on the given ECU, return {did: payload-or-None}."""
    if not protocol.switch_ecu(ecu):
        log(f"  !! Could not switch to {ecu.name} — skipping")
        return {did: None for did in dids}

    out: dict[str, str | None] = {}
    for did in dids:
        raw, ok = protocol.query_raw_logged(f"22{did}")
        out[did] = extract_payload(raw, did) if ok else None
        time.sleep(0.05)
    return out


def wait_for_state(state: str) -> None:
    log("")
    log("=" * 60)
    log(f">>> Brake state: {state.upper()}")
    if state == "idle":
        log("    Foot OFF the brake, car READY in D. Press ENTER when ready.")
    elif state == "light":
        log("    Light brake — pedal pressed gently (~25%). Hold steady,")
        log("    press ENTER when stable.")
    elif state == "hard":
        log("    Hard brake — pedal pressed firmly (~75%+). Hold steady,")
        log("    press ENTER when stable.")
    log("=" * 60)
    try:
        input()
    except EOFError:
        # If running non-interactively, give a brief delay so the operator
        # can position themselves.
        time.sleep(3)


def diff_table(label: str, dids: list[str],
               snaps: dict[str, dict[str, str | None]]) -> list[str]:
    """Print a side-by-side table. Returns list of monotonic DIDs."""
    log("")
    log(f"=== {label} ===")
    header = f"{'DID':<6} {'IDLE':<14} {'LIGHT':<14} {'HARD':<14} {'NOTE'}"
    log(header)
    log("-" * len(header))

    monotonic: list[str] = []
    for did in dids:
        cells = [snaps[s].get(did) for s in STATES]
        cell_strs = [(c or "—")[:12].ljust(12) for c in cells]
        note = classify(cells)
        if "MONOTONIC" in note:
            monotonic.append(did)
        log(f"{did:<6} {cell_strs[0]:<14} {cell_strs[1]:<14} {cell_strs[2]:<14} {note}")
    return monotonic


def classify(cells: list[str | None]) -> str:
    """Look at the first 2 bytes of each state; flag monotonic movement."""
    if any(c is None for c in cells):
        if all(c is None for c in cells):
            return "no response in any state"
        return "intermittent (NO DATA in some states)"

    if cells[0] == cells[1] == cells[2]:
        return "no change"

    # Try a few interpretations and report any that move monotonically.
    interpretations: list[tuple[str, list[int]]] = []
    for nbytes in (1, 2, 4):
        if all(len(c) >= nbytes * 2 for c in cells):
            try:
                vals = [int(c[:nbytes * 2], 16) for c in cells]
                interpretations.append((f"{nbytes}B", vals))
            except ValueError:
                pass

    notes: list[str] = []
    for label, vals in interpretations:
        if vals[0] <= vals[1] <= vals[2] and vals[0] < vals[2]:
            notes.append(f"{label} ↑ {vals[0]}→{vals[1]}→{vals[2]} (MONOTONIC UP)")
        elif vals[0] >= vals[1] >= vals[2] and vals[0] > vals[2]:
            notes.append(f"{label} ↓ {vals[0]}→{vals[1]}→{vals[2]} (MONOTONIC DOWN)")

    if notes:
        return " | ".join(notes)
    return "changed but non-monotonic"


def main() -> int:
    parser = argparse.ArgumentParser(description="ECU-F brake DID sweep (Research B)")
    parser.add_argument("--port", default="/dev/rfcomm0", help="Serial port")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(REPO_ROOT, "docs",
                            f"scan_results_ecu_f_brake_{timestamp}.log")
    log_path = os.path.abspath(log_path)

    log(f"ECU-F brake sweep + ECU-E FD0x re-read")
    log(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Port: {args.port}")
    log(f"ECU-F DIDs:  {', '.join(ECU_F_DIDS)}")
    log(f"ECU-E DIDs:  {', '.join(ECU_E_DIDS)}")
    log("")
    log("PRE-FLIGHT: car READY, gear in D, parking brake engaged or use")
    log("flat ground + foot brake. Stay stationary throughout.")

    bt = BluetoothConfig(port=args.port)
    connection = OBD2Connection(bt)
    protocol = ELMProtocol(connection)

    snaps_f: dict[str, dict[str, str | None]] = {}
    snaps_e: dict[str, dict[str, str | None]] = {}

    try:
        connection.connect()
        if not protocol.initialize():
            log("\n!! ELM327 init failed — aborting.")
            return 1

        for state in STATES:
            wait_for_state(state)

            log(f"\n--- Sampling ECU-F E300–E30F ({state}) ---")
            snaps_f[state] = snapshot(protocol, ECU_F, ECU_F_DIDS)
            for did, payload in snaps_f[state].items():
                log(f"  {did}: {payload if payload else 'NO DATA'}")

            log(f"\n--- Sampling ECU-E FD00–FD02 ({state}) ---")
            snaps_e[state] = snapshot(protocol, ECU_E, ECU_E_DIDS)
            for did, payload in snaps_e[state].items():
                log(f"  {did}: {payload if payload else 'NO DATA'}")

        mono_f = diff_table("ECU-F E300–E30F summary", ECU_F_DIDS, snaps_f)
        mono_e = diff_table("ECU-E FD00–FD02 summary", ECU_E_DIDS, snaps_e)

        log("")
        log("=" * 60)
        log("CANDIDATE SUMMARY")
        log("=" * 60)
        if mono_f or mono_e:
            log("Monotonic-with-brake DIDs (brake-pressure / brake-position candidates):")
            for did in mono_f:
                log(f"  • ECU-F  {did}")
            for did in mono_e:
                log(f"  • ECU-E  {did}")
            log("")
            log("Next step: re-run at finer brake granularity to confirm scaling,")
            log("then register the strongest candidate as `brake_pct` in obd2/pids.py.")
        else:
            log("No DID moved monotonically with brake force.")
            log("Next step: try Research B.3 (ECU-D extended session 1003)")
            log("or B.4 (ABS module address scan).")

    except KeyboardInterrupt:
        log("\n\nInterrupted by user.")
    except Exception:
        import traceback
        log(f"\nERROR:\n{traceback.format_exc()}")
        return 1
    finally:
        connection.disconnect()
        save_log(log_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
