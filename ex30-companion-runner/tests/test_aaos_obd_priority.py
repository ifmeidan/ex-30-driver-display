"""Verify the AAOS bridge wins over OBD2 for fields it covers when fresh,
and that OBD2 takes back over when AAOS goes stale."""

from unittest.mock import MagicMock

from config.settings import PollingIntervals
from obd2.poller import Poller
from obd2.protocol import ELMProtocol
from obd2.pids import PIDRegistry
from shared.vehicle_data import SharedVehicleData


def _poller(shared: SharedVehicleData) -> Poller:
    proto = MagicMock(spec=ELMProtocol)
    proto.initialized = True
    return Poller(proto, PIDRegistry(), PollingIntervals(), shared_data=shared)


def test_obd_writes_skipped_when_aaos_fresh():
    shared = SharedVehicleData()
    shared.update(aaos_stale=False, speed=42, soc=72.0, ambient_temp=18.5)
    poller = _poller(shared)

    poller._push_to_shared("speed", 90)
    poller._push_to_shared("soc_display", 55.0)
    poller._push_to_shared("outside_temp", 12.0)

    snap = shared.snapshot()
    assert snap.speed == 42        # OBD write suppressed
    assert snap.soc == 72.0
    assert snap.ambient_temp == 18.5


def test_obd_writes_resume_when_aaos_stale():
    shared = SharedVehicleData()
    shared.update(aaos_stale=True, speed=42, soc=72.0, ambient_temp=18.5)
    poller = _poller(shared)

    poller._push_to_shared("speed", 90)
    poller._push_to_shared("soc_display", 55.0)
    poller._push_to_shared("outside_temp", 12.0)

    snap = shared.snapshot()
    assert snap.speed == 90
    assert snap.soc == 55.0
    assert snap.ambient_temp == 12.0


def test_non_aaos_fields_always_write():
    """Battery temp, HV voltage/current, odometer have no AAOS counterpart —
    OBD2 should keep writing them regardless of bridge freshness."""
    shared = SharedVehicleData()
    shared.update(aaos_stale=False)
    poller = _poller(shared)

    poller._push_to_shared("hv_batt_temp_avg", 25.0)
    poller._push_to_shared("hv_voltage", 400.0)
    poller._push_to_shared("odometer", 12345)

    snap = shared.snapshot()
    assert snap.battery_temp == 25.0
    assert snap.hv_voltage == 400.0
    assert snap.odometer == 12345


def test_brake_gauge_averages_four_channels():
    """brake_pct = mean(FD00-FD03 bar) / 62, clamped to 0..1."""
    shared = SharedVehicleData()
    poller = _poller(shared)

    poller.latest.update({
        "brake_pressure": 20.0,
        "brake_pressure_b": 22.0,
        "brake_pressure_c": 18.0,
        "brake_pressure_d": 20.0,
    })
    poller._derive_brake()
    snap = shared.snapshot()
    assert abs(snap.brake_pct - (20.0 / 62.0)) < 1e-9
    assert abs(poller.latest["brake_avg_bar"] - 20.0) < 1e-9


def test_brake_gauge_clamps_at_full_scale():
    shared = SharedVehicleData()
    poller = _poller(shared)
    poller.latest.update({"brake_pressure": 70.0, "brake_pressure_b": 68.0})
    poller._derive_brake()
    assert shared.snapshot().brake_pct == 1.0


def test_brake_gauge_partial_channels():
    """Average works before all four channels have reported."""
    shared = SharedVehicleData()
    poller = _poller(shared)
    poller.latest["brake_pressure"] = 31.0
    poller._derive_brake()
    assert abs(shared.snapshot().brake_pct - 0.5) < 1e-9


def test_poller_lanes_only_active_pids():
    """2026-07-16 cleanup: lanes hold only brake, power backup, batt temp,
    odometer — all on the two 29-bit ECUs (BECM + ECU-E)."""
    shared = SharedVehicleData()
    poller = _poller(shared)

    lane_names = {p.name for pids in poller._ecu_groups.values() for p in pids}
    assert lane_names == {
        "brake_pressure_multi", "hv_current", "hv_voltage",
        "hv_batt_temp_avg", "odometer",
    }
    ecu_names = {e.name for e in poller._ecu_groups if e is not None}
    assert ecu_names == {"BECM", "ECU-E (Motor)"}
    assert None not in poller._ecu_groups  # no ELM-direct lane


def test_brake_gauge_prefers_multi_did_average():
    """When the multi-DID read works, its same-instant average drives the gauge."""
    shared = SharedVehicleData()
    poller = _poller(shared)
    poller.latest["brake_pressure_multi"] = 31.0
    # stale sequential value must not win
    poller.latest["brake_pressure"] = 5.0
    poller._derive_brake()
    assert abs(shared.snapshot().brake_pct - 0.5) < 1e-9


def test_brake_multi_falls_back_to_sequential():
    """5 consecutive multi-DID misses promote FD00-FD03 and retire the multi PID."""
    from obd2.pids import PollGroup

    shared = SharedVehicleData()
    poller = _poller(shared)
    # ECU answers with a 7F rejection: adapter response ok, decode fails.
    poller.protocol.query_raw_logged.return_value = ("7F2231", True)

    multi = poller.registry.get("brake_pressure_multi")
    for _ in range(poller.BRAKE_MULTI_MAX_FAILURES):
        poller._query_one(multi)

    assert poller._lanes_dirty
    assert multi.poll_group == PollGroup.NONE
    for name in poller._BRAKE_CHANNELS:
        assert poller.registry.get(name).poll_group == PollGroup.FAST

    poller._build_ecu_groups()
    lane_names = {p.name for pids in poller._ecu_groups.values() for p in pids}
    assert "brake_pressure_multi" not in lane_names
    assert {"brake_pressure", "brake_pressure_b",
            "brake_pressure_c", "brake_pressure_d"} <= lane_names


def test_brake_multi_no_data_does_not_trigger_fallback():
    """A sleeping ECU (NO DATA -> None) must not disable the multi-DID read."""
    from obd2.pids import PollGroup

    shared = SharedVehicleData()
    poller = _poller(shared)
    poller.protocol.query_raw_logged.return_value = ("NO DATA", False)  # car asleep

    multi = poller.registry.get("brake_pressure_multi")
    for _ in range(poller.BRAKE_MULTI_MAX_FAILURES * 2):
        poller._query_one(multi)

    assert not poller._lanes_dirty
    assert multi.poll_group == PollGroup.FAST
