"""Receiver-side smoke tests: feed protocol frames, assert SharedVehicleData."""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from aaos_bridge.receiver import AaosBridgeReceiver, WIRE_FIELDS
from shared.vehicle_data import SharedVehicleData


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def receiver(tmp_path):
    shared = SharedVehicleData()
    port = _free_port()
    rec = AaosBridgeReceiver(shared, host="127.0.0.1", port=port,
                             log_dir=str(tmp_path), stale_after_s=0.5)
    rec.start()
    # give the accept loop a moment to bind
    time.sleep(0.1)
    yield shared, rec, port, tmp_path
    rec.stop()


def _send(port: int, frames: list[dict]) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
        for frame in frames:
            s.sendall((json.dumps(frame) + "\n").encode("utf-8"))
        # let the receiver drain before close so we don't race the test asserts
        time.sleep(0.1)


def _wait_until(predicate, timeout=2.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_state_frame_updates_shared_data(receiver):
    shared, _, port, _ = receiver
    _send(port, [
        {"type": "hello", "ts": 1, "app_version": "0.1.0",
         "schema_version": 1, "fields_subscribed": ["gear"]},
        {"type": "state", "ts": 2, "src": "aaos", "fields": {
            "gear": "D",
            "parking_brake": False,
            "speed_aaos": 13.4,
            "soc_aaos": 32500.0,
            "ignition_state": "ON",
        }},
    ])
    assert _wait_until(lambda: shared.snapshot().gear == "D")
    snap = shared.snapshot()
    assert snap.gear == "D"
    assert snap.parking_brake is False
    assert abs(snap.speed_aaos - 13.4) < 1e-6
    assert abs(snap.soc_aaos - 32500.0) < 1e-6
    assert snap.ignition_state == "ON"
    assert snap.aaos_stale is False


def test_display_speed_is_the_only_speed_source(tmp_path):
    """The displayed `speed` comes ONLY from the dash-calibrated
    `speed_display_aaos`. A raw-only frame updates the `_aaos` diagnostic
    copy but must NEVER move the displayed speed (no fallback)."""
    shared = SharedVehicleData()
    port = _free_port()
    rec = AaosBridgeReceiver(shared, host="127.0.0.1", port=port,
                             log_dir=str(tmp_path), stale_after_s=999.0)
    rec.start()
    time.sleep(0.1)
    try:
        # Calibrated value drives the display.
        _send(port, [
            {"type": "state", "ts": 1, "src": "aaos", "fields": {
                "speed_display_aaos": 25.0,   # 90 km/h
            }},
        ])
        assert _wait_until(lambda: shared.snapshot().speed == 90)

        # A later raw-only frame must not touch the display, even though it
        # would decode to a very different speed if a fallback existed.
        _send(port, [
            {"type": "state", "ts": 2, "src": "aaos", "fields": {
                "speed_aaos": 5.0,            # would be 18 km/h via fallback
            }},
        ])
        # Wait for the raw copy to land, proving the frame was processed...
        assert _wait_until(lambda: abs(shared.snapshot().speed_aaos - 5.0) < 1e-6)
        # ...yet the displayed speed is unchanged.
        assert shared.snapshot().speed == 90
    finally:
        rec.stop()


def test_pack_capacity_calibrates_soc(tmp_path):
    """INFO_EV_BATTERY_CAPACITY arriving in the seed snapshot should
    re-calibrate the SoC % conversion away from the Settings default."""
    shared = SharedVehicleData()
    port = _free_port()
    # Constructor default 64 kWh; the wire frame will override it to 51 kWh
    # (Standard Range), so 25 500 Wh should read as 50% — not 39.8%.
    rec = AaosBridgeReceiver(shared, host="127.0.0.1", port=port,
                             log_dir=str(tmp_path), stale_after_s=999.0,
                             pack_capacity_wh=64000.0)
    rec.start()
    time.sleep(0.1)
    try:
        _send(port, [
            {"type": "state", "ts": 1, "src": "aaos", "fields": {
                "pack_capacity_wh": 51000.0,
                "soc_aaos": 25500.0,
            }},
        ])
        assert _wait_until(lambda: shared.snapshot().pack_capacity_wh == 51000.0)
        assert abs(shared.snapshot().soc - 50.0) < 1e-3
    finally:
        rec.stop()


def test_charge_port_connected_sets_is_charging(tmp_path):
    shared = SharedVehicleData()
    port = _free_port()
    rec = AaosBridgeReceiver(shared, host="127.0.0.1", port=port,
                             log_dir=str(tmp_path), stale_after_s=999.0)
    rec.start()
    time.sleep(0.1)
    try:
        _send(port, [
            {"type": "state", "ts": 1, "src": "aaos", "fields": {
                "charge_port_connected": True,
                # 50 kW DC fast charge = 50,000,000 mW. Wire field is signed
                # per VHAL spec: positive = charging (after AAOS-side flip).
                "battery_power_mw": 50_000_000.0,
            }},
        ])
        assert _wait_until(lambda: shared.snapshot().is_charging is True)
        assert abs(shared.snapshot().charge_power_kw - 50.0) < 1e-6
        # And it clears when the cable is unplugged.
        _send(port, [
            {"type": "state", "ts": 2, "src": "aaos", "fields": {
                "charge_port_connected": False,
            }},
        ])
        assert _wait_until(lambda: shared.snapshot().is_charging is False)
    finally:
        rec.stop()


def test_canonical_aliases_are_written(tmp_path):
    """AAOS-covered fields also land on the canonical OBD-shared attributes,
    converted to the units the UI expects (km/h, %, °C)."""
    shared = SharedVehicleData()
    port = _free_port()
    rec = AaosBridgeReceiver(shared, host="127.0.0.1", port=port,
                             log_dir=str(tmp_path), stale_after_s=999.0,
                             pack_capacity_wh=64000.0)
    rec.start()
    time.sleep(0.1)
    try:
        _send(port, [
            {"type": "state", "ts": 1, "src": "aaos", "fields": {
                "speed_display_aaos": 27.7778,   # 100 km/h (drives display)
                "speed_aaos": 10.0,              # 36 km/h raw — diagnostics only
                "soc_aaos": 32000.0,             # 50% of 64 kWh
                "ambient_temp_aaos": 18.5,
            }},
        ])
        assert _wait_until(lambda: shared.snapshot().speed == 100)
        snap = shared.snapshot()
        assert snap.speed == 100                  # km/h, int — from display, not raw
        assert abs(snap.soc - 50.0) < 1e-3        # %
        assert abs(snap.ambient_temp - 18.5) < 1e-6
        # _aaos copies are still present for divergence diagnostics
        assert abs(snap.speed_aaos - 10.0) < 1e-3
        assert abs(snap.soc_aaos - 32000.0) < 1e-3
    finally:
        rec.stop()


def test_unknown_fields_are_dropped(receiver):
    shared, _, port, _ = receiver
    _send(port, [
        {"type": "state", "ts": 1, "src": "aaos", "fields": {
            "gear": "P",
            "totally_made_up": "danger",
            "__class__": "evil",
        }},
    ])
    assert _wait_until(lambda: shared.snapshot().gear == "P")
    snap = shared.snapshot()
    # Whitelist must reject anything outside WIRE_FIELDS.
    assert not hasattr(snap, "totally_made_up")
    assert "totally_made_up" not in WIRE_FIELDS


def test_bad_json_line_does_not_kill_connection(receiver):
    shared, _, port, _ = receiver
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
        s.sendall(b"not json at all\n")
        s.sendall(json.dumps(
            {"type": "state", "ts": 1, "src": "aaos",
             "fields": {"gear": "R"}}
        ).encode() + b"\n")
        time.sleep(0.2)
    assert _wait_until(lambda: shared.snapshot().gear == "R")


def test_jsonl_log_is_written(receiver):
    shared, _, port, log_dir = receiver
    _send(port, [
        {"type": "heartbeat", "ts": 1},
        {"type": "state", "ts": 2, "src": "aaos", "fields": {"gear": "N"}},
    ])
    assert _wait_until(lambda: shared.snapshot().gear == "N")
    files = list(log_dir.glob("aaos_bridge_*.jsonl"))
    assert files, "expected a jsonl log file"
    contents = files[0].read_text(encoding="utf-8").splitlines()
    types = [json.loads(line)["type"] for line in contents]
    assert "heartbeat" in types
    assert "state" in types


def test_stale_flag_flips_after_silence(receiver):
    shared, _, port, _ = receiver
    _send(port, [
        {"type": "state", "ts": 1, "src": "aaos", "fields": {"gear": "D"}},
    ])
    assert _wait_until(lambda: shared.snapshot().aaos_stale is False)
    # receiver fixture configures stale_after_s=0.5 — wait a touch longer
    assert _wait_until(lambda: shared.snapshot().aaos_stale is True, timeout=2.0)


def test_new_connection_drops_old(receiver):
    shared, _, port, _ = receiver
    s1 = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    try:
        s1.sendall(json.dumps(
            {"type": "state", "ts": 1, "src": "aaos", "fields": {"gear": "P"}}
        ).encode() + b"\n")
        time.sleep(0.1)
        s2 = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        try:
            s2.sendall(json.dumps(
                {"type": "state", "ts": 2, "src": "aaos", "fields": {"gear": "D"}}
            ).encode() + b"\n")
            assert _wait_until(lambda: shared.snapshot().gear == "D")
        finally:
            s2.close()
    finally:
        s1.close()
