"""Trip-Log state machine.

A trip spans **charge-to-charge**: it begins when the car finishes charging
and accumulates km, energy and regen until the next plug-in. Baselines live
in a small JSON file on disk (`state/trip.json` by default) so power-cycling
the Pi mid-drive doesn't re-anchor the trip — only the next charge does.

Re-anchoring is driven by three independent triggers so the reset survives
the common case where the Pi is powered off for the whole charge (overnight
AC charging) and never observes a charging edge:

1. **Charge start** (`is_charging` rising edge) — anchor the odometer now and
   mark the start SoC *pending* (`None`); the car is still filling so we don't
   yet know the level the upcoming trip will begin at.
2. **Charge end** (`is_charging` falling edge) — fill the pending start SoC
   with the post-charge level.
3. **Missed-charge detector** — on boot, a large SoC jump with the odometer
   essentially unchanged means a charge happened while the app was off;
   re-anchor as if we'd caught the plug-out.

A pending start SoC that never got filled (caught plug-in, missed plug-out)
falls back to the latest known SoC — never 0, which just means the source
hasn't reported yet.

Trip regen is a **persisted accumulator**, not a baseline against the live
session integrator: the poller/receiver `regen_kwh` restarts at 0 on every
power cycle, so "session minus baseline" zeroes out whenever the screen
loses power mid-trip. Instead each frame's positive session delta is added
to `trip_regen_kwh`, which rides along in the same JSON file (60s throttled
save) and only resets on charge events.

The math is kept out of the Qt widgets so it can be exercised by plain
Python tests.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback when AAOS hasn't pushed INFO_EV_BATTERY_CAPACITY yet. Matches the
# Settings default for the EX30 Long Range usable pack.
DEFAULT_PACK_WH = 64000.0
# Minimum trip distance before efficiency stabilizes enough to display.
MIN_KM_FOR_EFFICIENCY = 1.0
# Default location for the persisted baseline.
DEFAULT_STATE_PATH = "state/trip.json"

# Missed-charge detector: a SoC rise of at least this many points with the
# odometer moving no more than CHARGE_JUMP_MAX_KM means a charge session
# happened while the app wasn't running. Normal driving only *drops* SoC;
# a >5-point *gain* needs either a charger or a very long downhill (which
# would move the odometer well past the km guard).
CHARGE_SOC_JUMP_PCT = 5.0
CHARGE_JUMP_MAX_KM = 3
# Throttle for persisting the last-seen soc/odo used by the jump detector.
# 60 s keeps SD-card writes rare while staying fresh enough that a shutdown
# loses at most a fraction of a percent of SoC.
_SAVE_MIN_INTERVAL_S = 60.0


@dataclass
class TripMetrics:
    """Snapshot of the values rendered into the Trip Log box."""
    km: Optional[float] = None
    kwh_used: Optional[float] = None
    efficiency_kwh_per_100km: Optional[float] = None
    range_to_10_km: Optional[float] = None
    range_to_0_km: Optional[float] = None
    regen_kwh: Optional[float] = None


class TripTracker:
    """Holds trip baselines and produces TripMetrics from each snapshot.

    Baselines persist across app restarts via a JSON file written every
    time a baseline field changes — i.e. on charge-end transitions and on
    cold-start initialization. Loads from the same file on construction.
    """

    def __init__(self, state_path: str = DEFAULT_STATE_PATH) -> None:
        self._state_path = state_path
        self._start_odo: Optional[int] = None
        self._start_soc: Optional[float] = None
        # Charge-to-charge regen accumulator (kWh). Persisted; survives the
        # session integrator restarting at 0 on every power cycle.
        self._trip_regen_kwh: float = 0.0
        # Last session regen_kwh seen this app run — in-memory on purpose:
        # a fresh process must not diff against a stale pre-reboot value.
        self._session_regen_prev: Optional[float] = None
        self._was_charging: bool = False
        # Last-seen observation, persisted so the missed-charge detector can
        # compare across an app restart / power cycle.
        self._last_soc: Optional[float] = None
        self._last_odo: Optional[int] = None
        self._last_save_t: float = 0.0
        self._load()

    # Test/debug accessors.
    @property
    def baseline(self) -> tuple[Optional[int], Optional[float], float]:
        return self._start_odo, self._start_soc, self._trip_regen_kwh

    # --- persistence -----------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("trip state unreadable (%s); starting fresh", exc)
            return
        if not isinstance(d, dict):
            return
        self._start_odo = d.get("start_odo")
        self._start_soc = d.get("start_soc")
        try:
            self._trip_regen_kwh = float(d.get("trip_regen_kwh") or 0.0)
        except (TypeError, ValueError):
            self._trip_regen_kwh = 0.0
        self._was_charging = bool(d.get("was_charging", False))
        self._last_soc = d.get("last_soc")
        self._last_odo = d.get("last_odo")
        logger.info(
            "loaded trip baseline odo=%s soc=%s regen=%.3f (last soc=%s odo=%s)",
            self._start_odo, self._start_soc, self._trip_regen_kwh,
            self._last_soc, self._last_odo,
        )

    def _save(self) -> None:
        payload = {
            "start_odo": self._start_odo,
            "start_soc": self._start_soc,
            "trip_regen_kwh": self._trip_regen_kwh,
            "was_charging": self._was_charging,
            "last_soc": self._last_soc,
            "last_odo": self._last_odo,
        }
        try:
            parent = os.path.dirname(self._state_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, self._state_path)
            self._last_save_t = time.monotonic()
        except OSError as exc:
            logger.warning("trip state save failed: %s", exc)

    def _maybe_save_last_seen(self) -> None:
        """Persist last-seen soc/odo, throttled to spare the SD card."""
        if time.monotonic() - self._last_save_t >= _SAVE_MIN_INTERVAL_S:
            self._save()

    # --- main update -----------------------------------------------------

    def update(
        self,
        *,
        odometer: int,
        soc: float,
        regen_kwh: float,
        is_charging: bool,
        range_m_aaos: float,
        aaos_stale: bool,
        pack_capacity_wh: float,
    ) -> TripMetrics:
        """Advance the state machine and return the metrics for this frame."""

        baseline_changed = False

        # Fold this frame's session-integrator movement into the trip
        # accumulator BEFORE the charge edges below get a chance to reset it —
        # regen earned on the way to the charger belongs to the ending trip.
        # A drop in the session value (integrator restarted, or an AAOS↔OBD
        # source handover) is never subtracted; we just re-sync the cursor.
        if (self._session_regen_prev is not None
                and regen_kwh > self._session_regen_prev):
            self._trip_regen_kwh += regen_kwh - self._session_regen_prev
        self._session_regen_prev = regen_kwh

        charge_started = is_charging and not self._was_charging
        charge_ended = self._was_charging and not is_charging
        self._was_charging = is_charging

        if charge_started:
            # A charge session began → the current trip is over. Anchor the
            # odometer now (the car won't move while plugged in) and leave the
            # start SoC pending; we don't know the post-charge level yet.
            self._start_odo = odometer
            self._start_soc = None
            self._trip_regen_kwh = 0.0
            baseline_changed = True

        if charge_ended:
            # Charge finished while we were watching: the post-charge SoC is
            # the trip's starting SoC. A 0 here means the source is silent on
            # this frame — leave SoC pending for the fallback below to fill.
            self._start_odo = odometer
            self._start_soc = soc if soc > 0 else None
            self._trip_regen_kwh = 0.0
            baseline_changed = True

        if not is_charging:
            # Missed-charge detector: the app was off for the whole charge, so
            # we saw no edge. A large SoC gain with the odometer barely moved
            # means a charge happened while we weren't looking — re-anchor as
            # if we'd caught the plug-out.
            jumped = (
                self._last_soc is not None and soc > 0
                and (soc - self._last_soc) >= CHARGE_SOC_JUMP_PCT
                and (self._last_odo is None
                     or abs(odometer - self._last_odo) <= CHARGE_JUMP_MAX_KM)
            )
            if jumped:
                self._start_odo = odometer
                self._start_soc = soc
                self._trip_regen_kwh = 0.0
                baseline_changed = True

            # Pending start SoC (caught plug-in, missed plug-out) — adopt the
            # latest known SoC. Never 0: that just means the source is silent.
            if self._start_soc is None and soc > 0:
                self._start_soc = soc
                baseline_changed = True

            # Cold-start init (first ever run, empty state file).
            if self._start_odo is None and odometer > 0:
                self._start_odo = odometer
                baseline_changed = True

        # Remember this frame for the next boot's jump detector. Guard against
        # transient zero reads (source not yet reporting) clobbering good data.
        if soc > 0:
            self._last_soc = soc
        if odometer > 0:
            self._last_odo = odometer

        if baseline_changed:
            self._save()
        else:
            self._maybe_save_last_seen()

        pack_kwh = (pack_capacity_wh or DEFAULT_PACK_WH) / 1000.0

        km: Optional[float] = None
        if self._start_odo is not None and odometer >= self._start_odo:
            km = float(odometer - self._start_odo)

        kwh_used: Optional[float] = None
        if self._start_soc is not None:
            kwh_used = max(0.0, (self._start_soc - soc) / 100.0 * pack_kwh)

        efficiency: Optional[float] = None
        if (km is not None and km >= MIN_KM_FOR_EFFICIENCY
                and kwh_used is not None and kwh_used > 0):
            efficiency = kwh_used / km * 100.0

        range_to_10: Optional[float] = None
        if efficiency is not None and efficiency > 0 and soc > 10:
            range_to_10 = (soc - 10) / 100.0 * pack_kwh / efficiency * 100.0

        range_to_0: Optional[float] = None
        if not aaos_stale and range_m_aaos > 0:
            range_to_0 = range_m_aaos / 1000.0

        return TripMetrics(
            km=km,
            kwh_used=kwh_used,
            efficiency_kwh_per_100km=efficiency,
            range_to_10_km=range_to_10,
            range_to_0_km=range_to_0,
            regen_kwh=self._trip_regen_kwh,
        )
