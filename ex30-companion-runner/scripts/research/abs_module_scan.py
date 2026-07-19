"""ABS / unknown-ECU address scan — Research B.4 + D.2/D.3.

Brake pressure is NOT on any known ECU (2B06-09 = wheel speeds, EE19/EE1A
ruled out), so the ABS module's address must be found first. This script
probes candidate 29-bit headers with a UDS ReadDataByIdentifier and reports
every address that answers — positive data OR a 7F negative response both
prove an ECU is listening there.

The response CAN ID (ATCRA) is DERIVED, not guessed. All five known ECUs
fit one formula exactly:

    CRA = 0x1EC02E80 + ((hdr16 - 0x1601) << 13)

    where hdr16 = low 16 bits of the D0xxxx header, e.g. D01635 -> 0x1635

    D01601 -> 1EC02E80 (VCFRONT)   D01635 -> 1EC6AE80 (BECM)
    D01637 -> 1EC6EE80 (ECU-F)     D01650 -> 1ECA0E80 (ECU-D)
    D01701 -> 1EE02E80 (ECU-E)

Usage:
  python scripts/abs_module_scan.py [--port /dev/rfcomm0]
                                    [--sweep 1602 17FF]
                                    [--test-did F190]

  Default: probes the built-in shortlist (ABS + BCM + BLIS
  candidates), ~30 s.
  --sweep START END: probe every header D0<START>..D0<END> (hex, low 16
  bits). The full 1602-17FF sweep is ~500 probes / roughly 15 minutes.

In the car: READY mode, gear D + parking brake, so chassis ECUs are awake.
Stop the display service first if it owns the dongle
(sudo systemctl stop driver-display).

Output: prints hits, saves full log to docs/scan_results_abs_scan_<ts>.log.
Next step after a hit: add the ECUContext to obd2/pids.py and sweep its
DIDs (2Bxx / 4Bxx / EExx ranges) at idle vs hard brake.
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
from obd2.pids import ECUContext


# Known ECUs — skipped during sweeps.
KNOWN_HDR16 = {0x1601, 0x1635, 0x1637, 0x1650, 0x1701}

# Built-in shortlist: §B.4 (ABS), §D.2 (BCM), §D.3 (BLIS).
DEFAULT_PROBES = [
    # ABS candidates
    0x1640, 0x1645, 0x1660, 0x1665, 0x1670, 0x1680,
    # BCM candidates
    0x16E0, 0x16F0, 0x1700, 0x1710, 0x1720,
    # BLIS radar candidates
    0x1780, 0x1790, 0x17A0,
]

_NRC = re.compile(r"7F(22|10|3E|11)")

_log_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    _log_lines.append(msg)


def save_log(path: str) -> None:
    with open(path, "w") as f:
        f.write("\n".join(_log_lines) + "\n")
    print(f"\nLog saved to: {path}")


def derive_cra(hdr16: int) -> int:
    return 0x1EC02E80 + ((hdr16 - 0x1601) << 13)


def build_context(hdr16: int) -> ECUContext:
    header = f"D0{hdr16:04X}"
    return ECUContext(
        name=f"probe-{header}",
        protocol=7,
        header=header,
        priority="1D",
        rx_filter=f"{derive_cra(hdr16):08X}",
        fc_header=f"1D{header}",
    )


def probe(protocol: ELMProtocol, hdr16: int,
          test_did: str) -> tuple[str, str] | None:
    """Probe one header. Returns (kind, detail) on a hit, None otherwise.

    kind: "DATA" for a positive 62<DID> response, "NRC" for a 7F negative
    response (ECU present but DID unsupported/secured).
    """
    ecu = build_context(hdr16)
    if not protocol.switch_ecu(ecu):
        return ("SWITCH-ERROR", "AT switch failed")

    raw, _ = protocol.query_raw_logged(f"22{test_did}")
    clean = (raw or "").replace(" ", "").upper()

    if f"62{test_did.upper()}" in clean:
        return ("DATA", raw.strip())
    if _NRC.search(clean):
        return ("NRC", raw.strip())
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="29-bit ECU address scan")
    parser.add_argument("--port", default="/dev/rfcomm0", help="Serial port")
    parser.add_argument("--sweep", nargs=2, metavar=("START", "END"),
                        help="Probe every header D0<START>..D0<END> "
                             "(hex low-16, e.g. --sweep 1602 17FF)")
    parser.add_argument("--test-did", default="F190",
                        help="DID for the probe query (default F190 = VIN, "
                             "widely supported)")
    args = parser.parse_args()

    if args.sweep:
        start, end = int(args.sweep[0], 16), int(args.sweep[1], 16)
        targets = [h for h in range(start, end + 1) if h not in KNOWN_HDR16]
    else:
        targets = DEFAULT_PROBES

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(REPO_ROOT, "docs",
                            f"scan_results_abs_scan_{timestamp}.log")

    log("29-bit ECU address scan (ABS / BCM / BLIS hunt)")
    log(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Port: {args.port}   Test DID: {args.test_did}")
    log(f"Targets: {len(targets)} headers "
        f"({'sweep' if args.sweep else 'built-in shortlist'})")
    log("")
    log("PRE-FLIGHT: car READY, gear D + parking brake (chassis ECUs sleep")
    log("outside drive-READY, same lesson as ECU-D/E/F).")
    log("")

    bt = BluetoothConfig(port=args.port)
    connection = OBD2Connection(bt)
    protocol = ELMProtocol(connection)

    hits: list[tuple[int, str, str]] = []

    try:
        connection.connect()
        if not protocol.initialize():
            log("!! ELM327 init failed — aborting.")
            return 1

        t0 = time.monotonic()
        for i, hdr16 in enumerate(targets, 1):
            header = f"D0{hdr16:04X}"
            result = probe(protocol, hdr16, args.test_did)
            if result:
                kind, detail = result
                if kind == "SWITCH-ERROR":
                    log(f"  [{i}/{len(targets)}] {header}: !! {detail}")
                    continue
                cra = derive_cra(hdr16)
                log(f"  [{i}/{len(targets)}] {header}: *** {kind} HIT *** "
                    f"(CRA {cra:08X}): {detail}")
                hits.append((hdr16, kind, detail))
            elif i % 16 == 0 or i == len(targets):
                elapsed = time.monotonic() - t0
                log(f"  [{i}/{len(targets)}] ...{header} silent "
                    f"({elapsed:.0f}s elapsed)")

        log("")
        log("=" * 60)
        log("SUMMARY")
        log("=" * 60)
        if hits:
            for hdr16, kind, detail in hits:
                log(f"  D0{hdr16:04X}  CRA {derive_cra(hdr16):08X}  [{kind}]  {detail}")
            log("")
            log("Next: add each hit as an ECUContext in obd2/pids.py, then")
            log("sweep its DIDs (2B00-2B1F, 4B00-4B1F, EE00-EE7F) at idle vs")
            log("hard brake to find the brake-pressure DIDs.")
        else:
            log("  No new ECU responded.")
            log("  Fallbacks: (a) btsnoop the reference scanner app with brake-pressure")
            log("  gauges displayed — the definitive path; (b) full sweep:")
            log("  python scripts/abs_module_scan.py --sweep 1602 17FF")

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
