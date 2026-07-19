"""Battery-temperature DID scan — Research A.

Sweeps the 4A20–4A3F range on both VCFRONT and BECM (where the BT snoop
showed 4A28–4A34 returning plausible cell-stack temperatures of
40.7 / 48.6 / 54.8 / 60.7 / 58.1 / 62.0 / 58.2 °C if raw÷10), plus a
wider BECM 4A80–4A8F pass and the suspect DD02 in the registry.

For each hit, prints multi-interpretation decodes so the operator can
eyeball which formula matches the dashboard battery-temp readout.

Usage:
  python scripts/becm_temp_scan.py [--port /dev/rfcomm0] [--watch SECS]

  --watch N   After the one-shot sweep, re-sample every 5 s for N seconds.
              Useful for catching a temp rise after a hard drive or
              fast-charge session.

Output: prints decoded table, saves the full log to
docs/scan_results_becm_temp_<timestamp>.log
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from config.settings import BluetoothConfig
from obd2.connection import OBD2Connection
from obd2.protocol import ELMProtocol
from obd2.pids import BECM, VCFRONT


# Ranges to sweep.
VCFRONT_RANGES = [(0x4A20, 0x4A3F)]
BECM_RANGES = [(0x4A20, 0x4A3F), (0x4A80, 0x4A8F)]
EXTRA_BECM_DIDS = ["DD02"]   # already in registry, reads stuck 59-60, cross-check

# Plausible HV-battery temperature window (°C) — anything outside is noise.
PLAUSIBLE_MIN = -20.0
PLAUSIBLE_MAX = 80.0


_log_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    _log_lines.append(msg)


def save_log(path: str) -> None:
    with open(path, "w") as f:
        f.write("\n".join(_log_lines) + "\n")
    print(f"\nLog saved to: {path}")


def extract_payload(raw: str, did: str) -> str | None:
    if not raw:
        return None
    clean = raw.replace(" ", "").replace("\r", "").replace("\n", "").upper()
    clean = re.sub(r"[0-9A-F]:", "", clean)
    marker = f"62{did.upper()}"
    idx = clean.find(marker)
    if idx == -1:
        return None
    return clean[idx + len(marker):]


def query_did(protocol: ELMProtocol, did: str) -> str | None:
    raw, ok = protocol.query_raw_logged(f"22{did}")
    if not ok:
        return None
    return extract_payload(raw, did)


def decode_candidates(payload: str) -> list[tuple[str, float]]:
    """Return [(formula_label, decoded_value), ...] for each plausible decode."""
    out: list[tuple[str, float]] = []
    if len(payload) >= 2:
        b0 = int(payload[:2], 16)
        out.append(("1B raw", float(b0)))
        out.append(("1B raw-40", float(b0 - 40)))
        out.append(("1B raw-50", float(b0 - 50)))
        out.append(("1B raw/2-40", b0 / 2.0 - 40))
    if len(payload) >= 4:
        u16 = int(payload[:4], 16)
        s16 = u16 if u16 < 32768 else u16 - 65536
        out.append(("2B raw/10", u16 / 10.0))
        out.append(("2B signed/10", s16 / 10.0))
        out.append(("2B raw*0.01", u16 * 0.01))
    if len(payload) >= 8:
        u32 = int(payload[:8], 16)
        out.append(("4B raw/100", u32 / 100.0))
    return out


def annotate(formula: str, value: float) -> str:
    marker = ""
    if PLAUSIBLE_MIN <= value <= PLAUSIBLE_MAX:
        marker = "  <-- plausible °C"
    return f"      {formula:<14} = {value:>10.2f}{marker}"


def sweep_range(protocol: ELMProtocol, ecu, start: int, end: int) -> dict[str, str]:
    """Sweep [start..end] inclusive on `ecu`. Returns {did: payload} for hits."""
    if not protocol.switch_ecu(ecu):
        log(f"  !! Could not switch to {ecu.name} — skipping {start:04X}-{end:04X}")
        return {}

    hits: dict[str, str] = {}
    for did_int in range(start, end + 1):
        did = f"{did_int:04X}"
        payload = query_did(protocol, did)
        if payload:
            hits[did] = payload
            log(f"  [HIT] {did}: {payload}")
            for formula, value in decode_candidates(payload):
                log(annotate(formula, value))
        time.sleep(0.05)
    return hits


def watch(protocol: ELMProtocol, targets: list[tuple[object, str]],
          duration: float) -> None:
    """Re-sample given (ecu, did) pairs every 5s for `duration` seconds."""
    log("")
    log("=" * 60)
    log(f">>> Watch mode — re-sampling every 5 s for {duration:.0f} s")
    log("=" * 60)
    header = "elapsed  " + "  ".join(f"{ecu.name[:6]}.{did}"
                                     for ecu, did in targets)
    log(header)

    start = time.monotonic()
    while time.monotonic() - start < duration:
        elapsed = time.monotonic() - start
        row = [f"{elapsed:>6.1f}s"]
        for ecu, did in targets:
            if not protocol.switch_ecu(ecu):
                row.append("SWITCH?")
                continue
            payload = query_did(protocol, did)
            if not payload:
                row.append("--")
                continue
            if len(payload) >= 4:
                u16 = int(payload[:4], 16)
                row.append(f"{u16/10:>6.1f}")
            elif len(payload) >= 2:
                row.append(f"{int(payload[:2], 16):>6d}")
            else:
                row.append(payload[:6])
        log("  ".join(row))
        time.sleep(5.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="HV battery temp DID scan")
    parser.add_argument("--port", default="/dev/rfcomm0", help="Serial port")
    parser.add_argument("--watch", type=float, default=0.0,
                        help="After sweep, re-sample plausible hits every 5s "
                             "for this many seconds (default 0 = off)")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(REPO_ROOT, "docs",
                            f"scan_results_becm_temp_{timestamp}.log")
    log_path = os.path.abspath(log_path)

    log("HV battery temperature DID scan")
    log(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Port: {args.port}")
    log("Plausible °C window: "
        f"{PLAUSIBLE_MIN:+.0f} to {PLAUSIBLE_MAX:+.0f}")
    log("")
    log("PRE-FLIGHT: car at READY (ignition on, foot on brake to wake")
    log("BECM/VCFRONT). For best signal, also note the dashboard battery")
    log("temperature so the right decode formula can be picked.")

    bt = BluetoothConfig(port=args.port)
    connection = OBD2Connection(bt)
    protocol = ELMProtocol(connection)

    plausible_targets: list[tuple[object, str]] = []

    try:
        connection.connect()
        if not protocol.initialize():
            log("\n!! ELM327 init failed — aborting.")
            return 1

        # --- VCFRONT 4A2x — strongest lead from snoop ---
        log("\n" + "=" * 60)
        log("VCFRONT (4A20–4A3F) — strongest snoop lead (4A28–4A34 active)")
        log("=" * 60)
        for start, end in VCFRONT_RANGES:
            hits = sweep_range(protocol, VCFRONT, start, end)
            for did, payload in hits.items():
                for formula, value in decode_candidates(payload):
                    if "2B raw/10" == formula and PLAUSIBLE_MIN <= value <= PLAUSIBLE_MAX:
                        plausible_targets.append((VCFRONT, did))
                        break

        # --- BECM 4A2x + 4A8x ---
        log("\n" + "=" * 60)
        log("BECM (4A20–4A3F, 4A80–4A8F)")
        log("=" * 60)
        for start, end in BECM_RANGES:
            log(f"\n--- BECM {start:04X}-{end:04X} ---")
            hits = sweep_range(protocol, BECM, start, end)
            for did, payload in hits.items():
                for formula, value in decode_candidates(payload):
                    if "2B raw/10" == formula and PLAUSIBLE_MIN <= value <= PLAUSIBLE_MAX:
                        plausible_targets.append((BECM, did))
                        break

        # --- BECM DD02 cross-check ---
        log("\n" + "=" * 60)
        log("BECM extra DIDs (cross-check)")
        log("=" * 60)
        if protocol.switch_ecu(BECM):
            for did in EXTRA_BECM_DIDS:
                payload = query_did(protocol, did)
                if payload:
                    log(f"  [HIT] {did}: {payload}")
                    for formula, value in decode_candidates(payload):
                        log(annotate(formula, value))
                else:
                    log(f"  {did}: NO DATA")

        # --- Summary ---
        log("\n" + "=" * 60)
        log("CANDIDATE SUMMARY")
        log("=" * 60)
        if plausible_targets:
            log("DIDs whose 2-byte raw/10 decode lands inside a plausible °C window:")
            for ecu, did in plausible_targets:
                log(f"  • {ecu.name}  {did}  (raw/10 in [{PLAUSIBLE_MIN:.0f}, {PLAUSIBLE_MAX:.0f}])")
            log("")
            log("Next step: compare against dashboard battery temp. If any DID")
            log("matches, register it in obd2/pids.py as `battery_temp` (replacing")
            log("the suspect DD02 candidate).")
        else:
            log("No DID returned a plausible °C value via raw/10 decode.")
            log("Check the raw 1-byte decodes above — Volvo may use raw-40.")

        if args.watch > 0 and plausible_targets:
            watch(protocol, plausible_targets, args.watch)

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
