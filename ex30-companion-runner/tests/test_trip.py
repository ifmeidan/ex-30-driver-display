"""TripTracker math and persistence — pure Python, no Qt.

Covers:
- Cold-start baseline init (first non-zero odo/SoC adopted).
- km / kWh / efficiency / range-to-10% math against hand-computed values.
- Range-to-0% pulled from AAOS range only when the source is fresh.
- Charge-end transition re-anchors the baseline.
- Cold-start init suppressed while charging (no mid-charge anchoring).
- Trip-regen accumulates session deltas into a persisted counter that
  survives the session integrator restarting at 0 (power cycle) and only
  resets on charge events.
- Baselines round-trip through state/trip.json so a fresh tracker
  picks up where the previous one left off (charge-to-charge).
"""

from __future__ import annotations

import json
import math
import os

import pytest

from ui.trip import TripTracker, DEFAULT_PACK_WH, MIN_KM_FOR_EFFICIENCY


PACK_WH = 64_000.0  # EX30 Long Range


def _state_file(tmp_path) -> str:
    return os.path.join(str(tmp_path), "trip.json")


def _frame(**overrides) -> dict:
    """A vehicle-data frame with sensible defaults for tests."""
    return {
        "odometer": 10_000,
        "soc": 100.0,
        "regen_kwh": 0.0,
        "is_charging": False,
        "range_m_aaos": 450_000.0,
        "aaos_stale": False,
        "pack_capacity_wh": PACK_WH,
        **overrides,
    }


# --- baseline + math ----------------------------------------------------


def test_cold_start_adopts_first_observation(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    m = tracker.update(**_frame(odometer=12_345, soc=88.0))
    # Baseline = the frame itself, so km/kWh start at 0.
    assert tracker.baseline == (12_345, 88.0, 0.0)
    assert m.km == 0.0
    assert m.kwh_used == 0.0
    # Efficiency hidden until we have meaningful distance.
    assert m.efficiency_kwh_per_100km is None
    # 0% range comes straight from AAOS.
    assert m.range_to_0_km == pytest.approx(450.0)


def test_cold_start_skipped_while_charging(tmp_path):
    """Booting plugged-in anchors the odometer but leaves start SoC pending —
    it must not snapshot a mid-charge SoC as the trip baseline."""
    tracker = TripTracker(_state_file(tmp_path))
    m = tracker.update(**_frame(is_charging=True, odometer=10_000, soc=60.0))
    # Odometer anchored (car won't move while plugged in); SoC still pending.
    assert tracker.baseline == (10_000, None, 0.0)
    assert m.km == 0.0        # odo baseline known
    assert m.kwh_used is None  # SoC baseline pending → no energy yet


def test_km_kwh_efficiency_after_movement(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(odometer=10_000, soc=100.0))
    # 50 km later, SoC dropped 12.5 pp (= 8.0 kWh of a 64 kWh pack).
    m = tracker.update(**_frame(odometer=10_050, soc=87.5))
    assert m.km == 50.0
    assert m.kwh_used == pytest.approx(8.0)
    # 8.0 kWh / 50 km × 100 = 16.0 kWh per 100 km.
    assert m.efficiency_kwh_per_100km == pytest.approx(16.0)
    # Range to 10% = (87.5 - 10) / 100 × 64 / 16 × 100 = 310.0 km.
    assert m.range_to_10_km == pytest.approx(310.0)


def test_efficiency_hidden_below_minimum_distance(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(odometer=10_000, soc=100.0))
    m = tracker.update(**_frame(odometer=10_000, soc=99.5))  # 0 km
    assert m.km == 0.0
    assert m.efficiency_kwh_per_100km is None
    assert m.range_to_10_km is None
    # Min-distance guard is what protects against /0 here.
    assert MIN_KM_FOR_EFFICIENCY > 0.0


def test_kwh_clamped_when_regen_outpaces_draw(tmp_path):
    """Net positive SoC during a trip (downhill regen) shouldn't push kWh negative."""
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(odometer=10_000, soc=80.0))
    m = tracker.update(**_frame(odometer=10_005, soc=81.0))  # SoC went UP
    assert m.kwh_used == 0.0


def test_range_to_0_requires_fresh_aaos(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    m = tracker.update(**_frame(aaos_stale=True))
    assert m.range_to_0_km is None
    m = tracker.update(**_frame(aaos_stale=False, range_m_aaos=123_000.0))
    assert m.range_to_0_km == pytest.approx(123.0)


def test_range_to_10_skipped_below_10pct(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(odometer=10_000, soc=100.0))
    # 200 km later, 9% SoC.
    m = tracker.update(**_frame(odometer=10_200, soc=9.0))
    assert m.range_to_10_km is None


def test_pack_capacity_defaults_when_zero(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(odometer=10_000, soc=100.0, pack_capacity_wh=0.0))
    m = tracker.update(
        **_frame(odometer=10_050, soc=87.5, pack_capacity_wh=0.0)
    )
    # Falls back to DEFAULT_PACK_WH (64 kWh) and still computes.
    expected_kwh = 0.125 * DEFAULT_PACK_WH / 1000.0
    assert m.kwh_used == pytest.approx(expected_kwh)


# --- regen tracking -----------------------------------------------------


def test_regen_accumulates_from_baseline(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    # First frame just syncs the cursor to the session integrator (12.0);
    # only movement observed after that counts toward the trip.
    tracker.update(**_frame(regen_kwh=12.0))
    m = tracker.update(**_frame(regen_kwh=15.4))
    assert m.regen_kwh == pytest.approx(3.4)


def test_regen_clamped_against_session_reset(tmp_path):
    """If session regen drops (poller restart), trip regen stays at 0 not negative."""
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(regen_kwh=8.0))
    m = tracker.update(**_frame(regen_kwh=0.5))
    assert m.regen_kwh == 0.0


def test_regen_survives_power_cycle(tmp_path, monkeypatch):
    """The original bug: the session integrator restarts at 0 on every power
    cut, so trip regen must come from a persisted accumulator, not from
    "session minus baseline"."""
    monkeypatch.setattr("ui.trip._SAVE_MIN_INTERVAL_S", 0.0)
    path = _state_file(tmp_path)
    tracker_a = TripTracker(path)
    tracker_a.update(**_frame(regen_kwh=0.0))
    m = tracker_a.update(**_frame(regen_kwh=1.5))
    assert m.regen_kwh == pytest.approx(1.5)

    # Screen loses power mid-trip; next boot the session integrator is back
    # at 0 but the trip total must carry on from 1.5.
    tracker_b = TripTracker(path)
    m = tracker_b.update(**_frame(regen_kwh=0.0))
    assert m.regen_kwh == pytest.approx(1.5)
    m = tracker_b.update(**_frame(regen_kwh=0.7))
    assert m.regen_kwh == pytest.approx(2.2)


def test_regen_resets_on_charge_events(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(regen_kwh=0.0))
    tracker.update(**_frame(regen_kwh=3.0))
    # Plug in → trip over, accumulator cleared.
    m = tracker.update(**_frame(regen_kwh=3.0, soc=60.0, is_charging=True))
    assert m.regen_kwh == 0.0
    # Plug out → still 0, then the next drive accumulates from here.
    m = tracker.update(**_frame(regen_kwh=3.0, soc=90.0, is_charging=False))
    assert m.regen_kwh == 0.0
    m = tracker.update(**_frame(regen_kwh=4.2, soc=89.0))
    assert m.regen_kwh == pytest.approx(1.2)


# --- charge-end re-anchor -----------------------------------------------


def test_charge_end_reanchors_all_baselines(tmp_path):
    tracker = TripTracker(_state_file(tmp_path))
    # Trip in progress before plug-in.
    tracker.update(**_frame(odometer=10_300, soc=42.0, regen_kwh=4.1))
    # Plug in, charge for a bit (mid-charge frames don't anchor anything).
    tracker.update(**_frame(odometer=10_300, soc=70.0, regen_kwh=4.1,
                            is_charging=True))
    pre_baseline = tracker.baseline
    tracker.update(**_frame(odometer=10_300, soc=95.0, regen_kwh=4.1,
                            is_charging=True))
    assert tracker.baseline == pre_baseline  # untouched while charging
    # Plug out at 95% — this is the new trip baseline, regen counter cleared.
    tracker.update(**_frame(odometer=10_300, soc=95.0, regen_kwh=4.1,
                            is_charging=False))
    assert tracker.baseline == (10_300, 95.0, 0.0)
    # Drive 100 km, lose 25% SoC, accumulate 2.5 kWh regen.
    m = tracker.update(**_frame(odometer=10_400, soc=70.0, regen_kwh=6.6))
    assert m.km == 100.0
    assert m.kwh_used == pytest.approx(0.25 * PACK_WH / 1000.0)
    assert m.regen_kwh == pytest.approx(2.5)


# --- charge-start anchoring + missed-charge recovery --------------------


def test_charge_start_anchors_odo_and_pends_soc(tmp_path):
    """Plug-in re-anchors the odometer immediately and marks SoC pending."""
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(odometer=10_300, soc=42.0, regen_kwh=4.1))
    # Plug in at 42% — trip is over, but the post-charge SoC isn't known yet.
    tracker.update(**_frame(odometer=10_300, soc=42.0, regen_kwh=4.1,
                            is_charging=True))
    start_odo, start_soc, trip_regen = tracker.baseline
    assert start_odo == 10_300
    assert start_soc is None        # pending until charge ends
    assert trip_regen == 0.0        # plug-in ends the trip → counter cleared


def test_pending_soc_filled_when_plugout_missed(tmp_path):
    """Caught the plug-in but missed the plug-out: adopt the live SoC on the
    next non-charging frame instead of leaving the trip un-computable."""
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(odometer=10_300, soc=42.0, is_charging=True))
    assert tracker.baseline[1] is None
    # App keeps running; charging flag drops but we never saw the transition
    # frame carry the final SoC — next frame is already unplugged at 88%.
    m = tracker.update(**_frame(odometer=10_300, soc=88.0, is_charging=False))
    assert tracker.baseline[1] == 88.0
    assert m.kwh_used == 0.0


def test_pending_soc_ignores_zero_reading(tmp_path):
    """A 0 SoC (source not reporting yet) must not become the baseline."""
    tracker = TripTracker(_state_file(tmp_path))
    tracker.update(**_frame(odometer=10_300, soc=42.0, is_charging=True))
    tracker.update(**_frame(odometer=10_300, soc=0.0, is_charging=False))
    assert tracker.baseline[1] is None  # still pending, 0 rejected
    tracker.update(**_frame(odometer=10_300, soc=90.0, is_charging=False))
    assert tracker.baseline[1] == 90.0


def test_missed_charge_reanchors_on_soc_jump(tmp_path):
    """App off for the whole charge (never sees an edge): a big SoC jump with
    the car stationary re-anchors the trip on the next boot."""
    path = _state_file(tmp_path)
    tracker = TripTracker(path)
    # End of a drive: parked at 30%, odo 10_500.
    tracker.update(**_frame(odometer=10_500, soc=30.0, regen_kwh=5.0))
    # Simulate a power cycle across an overnight charge to 80%.
    tracker_b = TripTracker(path)
    m = tracker_b.update(**_frame(odometer=10_500, soc=80.0, regen_kwh=5.0))
    # Re-anchored: baseline SoC is the post-charge 80%, so kWh starts at 0
    # and the regen counter is cleared for the new trip.
    assert tracker_b.baseline == (10_500, 80.0, 0.0)
    assert m.kwh_used == 0.0


def test_soc_rise_while_driving_is_not_a_charge(tmp_path):
    """Regen gains during a drive (odometer moving) must not trip the
    missed-charge detector and blow away the trip baseline."""
    path = _state_file(tmp_path)
    tracker = TripTracker(path)
    tracker.update(**_frame(odometer=10_500, soc=30.0))
    tracker_b = TripTracker(path)
    # +6% SoC but 20 km further down the road → real driving, not a charge.
    tracker_b.update(**_frame(odometer=10_520, soc=36.0))
    assert tracker_b.baseline[0] == 10_500  # baseline odo unchanged
    assert tracker_b.baseline[1] == 30.0    # baseline soc unchanged


# --- persistence --------------------------------------------------------


def test_baseline_round_trips_through_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("ui.trip._SAVE_MIN_INTERVAL_S", 0.0)
    path = _state_file(tmp_path)
    tracker_a = TripTracker(path)
    tracker_a.update(**_frame(odometer=10_000, soc=90.0, regen_kwh=0.0))
    assert os.path.exists(path), "baseline should have been written on first update"
    tracker_a.update(**_frame(odometer=10_010, soc=88.0, regen_kwh=1.5))

    # File contents reflect the in-memory state (plus last-seen tracking
    # fields the missed-charge detector relies on).
    with open(path) as f:
        on_disk = json.load(f)
    assert on_disk["start_odo"] == 10_000
    assert on_disk["start_soc"] == 90.0
    assert on_disk["trip_regen_kwh"] == 1.5

    # A fresh tracker (simulating an app restart) loads the same state.
    tracker_b = TripTracker(path)
    assert tracker_b.baseline == (10_000, 90.0, 1.5)
    # And continues the trip — at 10_030 km / 83% total, with the session
    # regen integrator restarted at 0 and adding another 0.5 kWh.
    tracker_b.update(**_frame(odometer=10_030, soc=83.0, regen_kwh=0.0))
    m = tracker_b.update(**_frame(odometer=10_030, soc=83.0, regen_kwh=0.5))
    assert m.km == 30.0
    assert m.kwh_used == pytest.approx(0.07 * PACK_WH / 1000.0)
    assert m.regen_kwh == pytest.approx(2.0)


def test_corrupt_state_file_starts_fresh(tmp_path):
    path = _state_file(tmp_path)
    with open(path, "w") as f:
        f.write("{not json")
    tracker = TripTracker(path)
    assert tracker.baseline == (None, None, 0.0)
    # And recovers normally on next update.
    tracker.update(**_frame(odometer=10_000, soc=88.0))
    assert tracker.baseline == (10_000, 88.0, 0.0)


def test_charge_end_persists_new_baseline(tmp_path):
    path = _state_file(tmp_path)
    tracker = TripTracker(path)
    tracker.update(**_frame(odometer=10_000, soc=100.0, regen_kwh=0.0))
    tracker.update(**_frame(odometer=10_050, soc=88.0, regen_kwh=1.0,
                            is_charging=True))
    tracker.update(**_frame(odometer=10_050, soc=100.0, regen_kwh=1.0,
                            is_charging=False))
    # New baseline anchored at the plug-out moment, regen counter cleared.
    with open(path) as f:
        on_disk = json.load(f)
    assert on_disk["start_odo"] == 10_050
    assert on_disk["start_soc"] == 100.0
    assert on_disk["trip_regen_kwh"] == 0.0
