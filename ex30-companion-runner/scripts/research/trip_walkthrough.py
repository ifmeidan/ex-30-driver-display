"""Frame-by-frame Trip Log walkthrough — no UI, prints metrics as the
tracker sees them.

Run from repo root:
    .venv/bin/python scripts/trip_walkthrough.py

Uses a throwaway state file in /tmp so it doesn't clobber state/trip.json.
"""

from __future__ import annotations

import os
import tempfile

from ui.trip import TripTracker, TripMetrics


def _fmt(v, suffix="", decimals=1):
    if v is None:
        return "—"
    if decimals == 0:
        return f"{int(v)}{suffix}"
    return f"{v:.{decimals}f}{suffix}"


def _row(label: str, frame: dict, m: TripMetrics) -> str:
    return (
        f"{label:<22}  "
        f"odo={frame['odometer']:>6}  "
        f"soc={frame['soc']:>5.1f}%  "
        f"regen={frame['regen_kwh']:>4.1f}  "
        f"charging={'Y' if frame['is_charging'] else 'N'}  "
        f"║  "
        f"km={_fmt(m.km, ' km', 0):>8}  "
        f"used={_fmt(m.kwh_used, ' kWh'):>10}  "
        f"eff={_fmt(m.efficiency_kwh_per_100km, ' kWh/100km'):>16}  "
        f"r10={_fmt(m.range_to_10_km, ' km', 0):>8}  "
        f"r0={_fmt(m.range_to_0_km, ' km', 0):>8}  "
        f"regen={_fmt(m.regen_kwh, ' kWh'):>10}"
    )


def main() -> None:
    base = {
        "is_charging": False,
        "range_m_aaos": 450_000.0,
        "aaos_stale": False,
        "pack_capacity_wh": 64_000.0,
    }

    # (label, frame-overrides) — written so each line tells a story.
    timeline = [
        ("0. fresh boot, unplug", dict(odometer=10_000, soc=100.0, regen_kwh=0.0)),
        ("1. 10 km coast",        dict(odometer=10_010, soc=98.0, regen_kwh=0.2)),
        ("2. 50 km cruise",       dict(odometer=10_050, soc=87.5, regen_kwh=1.6)),
        ("3. 200 km mixed",       dict(odometer=10_200, soc=55.0, regen_kwh=5.8)),
        ("4. plug in (mid-trip)", dict(odometer=10_200, soc=55.0, regen_kwh=5.8, is_charging=True)),
        ("5. charging up",        dict(odometer=10_200, soc=85.0, regen_kwh=5.8, is_charging=True)),
        ("6. plug out → reset",   dict(odometer=10_200, soc=95.0, regen_kwh=5.8, is_charging=False)),
        ("7. 30 km after charge", dict(odometer=10_230, soc=88.0, regen_kwh=6.5)),
        ("8. 120 km later",       dict(odometer=10_320, soc=55.0, regen_kwh=8.9)),
    ]

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "trip.json")
        tracker = TripTracker(path)

        header = (
            f"{'step':<22}  "
            f"{'odo':>10}  "
            f"{'soc':>6}  "
            f"{'regen':>5}  "
            f"{'chg':>9}  "
            f"║  "
            f"{'km':>8}  "
            f"{'used':>10}  "
            f"{'efficiency':>16}  "
            f"{'r10':>8}  "
            f"{'r0':>8}  "
            f"{'regen':>10}"
        )
        print(header)
        print("─" * len(header))

        for label, overrides in timeline:
            frame = {**base, **overrides}
            metrics = tracker.update(**frame)
            print(_row(label, frame, metrics))

        print()
        print("Final on-disk state:")
        with open(path) as f:
            print(" ", f.read().strip())

        # Simulate a reboot — fresh tracker loads the persisted baseline
        # and the trip continues from where it left off.
        print()
        print("--- simulated reboot ---")
        tracker2 = TripTracker(path)
        print(f"  loaded baseline: {tracker2.baseline}")
        frame = {**base, "odometer": 10_400, "soc": 38.0, "regen_kwh": 11.2}
        m = tracker2.update(**frame)
        print(_row("9. after reboot, 80km", frame, m))


if __name__ == "__main__":
    main()
