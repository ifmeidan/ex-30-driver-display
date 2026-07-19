"""BECM DID page sweep with temperature-signature matching — Research A.

The reference scanner app shows HV battery temps the known candidates can't explain
(avg 29.59 / max 30.19 / connector 28.69 on 2026-07-15; DD02-40=14,
4A35/100=19.0 all wrong). Two-decimal precision implies a u16 encoding:
2959/3019/2869 if x0.01, or 3027/3050/3018 if deci-Kelvin. This sweeps
whole DID pages on the BECM (or another ECU), decodes every positive
response, and flags payloads matching the reference app’s values.

Usage:
  python scripts/becm_did_sweep.py [--port /dev/rfcomm0]
      [--ecu BECM|VCFRONT|ECU-D|ECU-E|ECU-F]
      [--ranges 4800-48FF,4900-49FF,4A00-4AFF,4B00-4BFF,DA00-DAFF,DD00-DDFF]
      [--targets 29.59,30.19,28.69] [--tolerance 2.5]

Default ranges = the pages where BECM data is known to live (4801/4802
HV volt/amp, 4907/4908, 4A23, DA17, DD01/DD02). ~1500 DIDs; BECM answers
unsupported DIDs with a fast 7F NRC, so expect ~3 DIDs/s (~10-15 min).

Battery temp does NOT need drive-READY (BECM answers with car asleep).
Dongle must be free: close the reference scanner app, stop driver-display first.

Output: every positive response printed with all decode attempts,
*** MATCH *** on target hits. Full log: docs/scan_results_did_sweep_<ts>.log
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

ECUS = {
    "BECM": pids.BECM,
    "VCFRONT": pids.VCFRONT,
    "ECU-D": pids.ECU_D,
    "ECU-E": pids.ECU_E,
    "ECU-F": pids.ECU_F,
}

DEFAULT_RANGES = "4800-48FF,4900-49FF,4A00-4AFF,4B00-4BFF,DA00-DAFF,DD00-DDFF"

_log_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    _log_lines.append(msg)


def decode_all(payload: str) -> list[tuple[str, float]]:
    """All plausible temperature decodes of a hex payload.

    Returns (label, celsius) pairs. For payloads >2 bytes, also decodes
    the u16 tail (VCFRONT 4A2x pack values in the LAST two bytes).
    """
    out: list[tuple[str, float]] = []
    n = len(payload) // 2
    if n == 0:
        return out

    if n == 1:
        v = int(payload, 16)
        out.append(("u8", float(v)))
        out.append(("u8-40", float(v - 40)))
        out.append(("u8-50", float(v - 50)))
        out.append(("u8/2-40", v / 2 - 40))
        return out

    words = [("u16", int(payload[:4], 16))]
    if n > 2:
        words.append(("tail-u16", int(payload[-4:], 16)))
    for where, w in words:
        out.append((f"{where}", float(w)))
        out.append((f"{where}/10", w / 10))
        out.append((f"{where}/100", w / 100))
        out.append((f"{where}-2731/10", (w - 2731) / 10))  # deci-Kelvin
        out.append((f"{where}-400/10", (w - 400) / 10))
        out.append((f"{where}/64-40", w / 64 - 40))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="BECM DID page sweep")
    parser.add_argument("--port", default="/dev/rfcomm0")
    parser.add_argument("--ecu", default="BECM", choices=sorted(ECUS))
    parser.add_argument("--ranges", default=DEFAULT_RANGES,
                        help="Comma-separated hex ranges, e.g. 4800-48FF,DD00-DDFF")
    parser.add_argument("--targets", default="29.59,30.19,28.69",
                        help="Reference temps (deg C) from the reference scanner app")
    parser.add_argument("--tolerance", type=float, default=2.5,
                        help="Match window in deg C (default 2.5)")
    args = parser.parse_args()

    targets = [float(t) for t in args.targets.split(",") if t.strip()]
    dids: list[int] = []
    for rng in args.ranges.split(","):
        start_s, _, end_s = rng.strip().partition("-")
        start, end = int(start_s, 16), int(end_s or start_s, 16)
        dids.extend(range(start, end + 1))

    ecu = ECUS[args.ecu]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(REPO_ROOT, "docs",
                            f"scan_results_did_sweep_{timestamp}.log")

    log(f"DID page sweep on {args.ecu} ({ecu.header})")
    log(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Ranges: {args.ranges}  ({len(dids)} DIDs)")
    log(f"Targets: {targets} +/- {args.tolerance} degC")
    log("")

    bt = BluetoothConfig(port=args.port)
    connection = OBD2Connection(bt)
    protocol = ELMProtocol(connection)

    hits: list[tuple[str, str]] = []      # (did, payload) all positives
    matches: list[tuple[str, str, str]] = []  # (did, payload, decode-label)

    try:
        connection.connect()
        if not protocol.initialize():
            log("!! ELM327 init failed - aborting.")
            return 1
        if not protocol.switch_ecu(ecu):
            log(f"!! Switch to {args.ecu} failed - aborting.")
            return 1

        t0 = time.monotonic()
        for i, did in enumerate(dids, 1):
            did_hex = f"{did:04X}"
            raw, _ = protocol.query_raw_logged(f"22{did_hex}")
            clean = (raw or "").replace(" ", "").upper()
            marker = f"62{did_hex}"
            if marker in clean:
                payload = clean.split(marker, 1)[1]
                # Trim ELM prompt/whitespace artifacts, keep hex only
                payload = "".join(c for c in payload if c in "0123456789ABCDEF")
                decodes = decode_all(payload)
                matched = [
                    lbl for lbl, val in decodes
                    if any(abs(val - t) <= args.tolerance for t in targets)
                ]
                hits.append((did_hex, payload))
                pretty = "  ".join(
                    f"{lbl}={val:.2f}" for lbl, val in decodes
                    if -50 <= val <= 120
                )
                if matched:
                    matches.append((did_hex, payload, ",".join(matched)))
                    log(f"  [{i}/{len(dids)}] {did_hex} = {payload}"
                        f"  *** MATCH ({','.join(matched)}) ***  {pretty}")
                else:
                    log(f"  [{i}/{len(dids)}] {did_hex} = {payload}  {pretty}")
            elif i % 64 == 0 or i == len(dids):
                elapsed = time.monotonic() - t0
                rate = i / elapsed if elapsed else 0
                eta = (len(dids) - i) / rate if rate else 0
                log(f"  [{i}/{len(dids)}] ...{did_hex}"
                    f"  ({elapsed:.0f}s, {rate:.1f}/s, ETA {eta/60:.0f}m)")

        log("")
        log("=" * 60)
        log(f"SUMMARY: {len(hits)} DIDs answered, {len(matches)} matched targets")
        log("=" * 60)
        for did_hex, payload, lbl in matches:
            log(f"  *** {did_hex} = {payload}  ({lbl})")
        if not matches:
            log("  No target match. Re-check the reference scanner app value (temp drifts)")
            log("  and consider sweeping VCFRONT: --ecu VCFRONT")

    except KeyboardInterrupt:
        log("\n\nInterrupted by user.")
    except Exception:
        import traceback
        log(f"\nERROR:\n{traceback.format_exc()}")
        return 1
    finally:
        connection.disconnect()
        with open(log_path, "w") as f:
            f.write("\n".join(_log_lines) + "\n")
        print(f"\nLog saved to: {log_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
