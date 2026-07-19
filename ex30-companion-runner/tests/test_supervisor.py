"""ObdSupervisor state machine — white-box tick tests, no threads.

Each test drives `_tick()` directly and backdates the monotonic anchors
(`_last_active_mono`, `_start_mono`, LinkHealth stamps) instead of
sleeping. Connection/protocol/poller are mocks; SharedVehicleData is real
so activity detection reads genuine snapshots.
"""

import time
from unittest.mock import MagicMock

from config.settings import StandbyConfig
from obd2.supervisor import LinkHealth, ObdSupervisor, _State
from shared.vehicle_data import SharedVehicleData


class FakeStatus:
    def __init__(self):
        self.states = []

    def set_obd(self, state):
        self.states.append(state)

    def last(self):
        return self.states[-1] if self.states else None


def _make_sup(shared=None, *, connect_ok=True, cfg=None):
    connection = MagicMock()
    protocol = MagicMock()
    protocol.initialize = MagicMock(return_value=connect_ok)
    poller = MagicMock()
    shared = shared if shared is not None else SharedVehicleData()
    health = LinkHealth()
    cfg = cfg or StandbyConfig(min_uptime_s=0.0, idle_after_s=60.0,
                               aaos_gone_s=60.0, link_dead_s=5.0)
    status = FakeStatus()
    standby_calls = []
    sup = ObdSupervisor(
        connection, protocol, poller, shared, health, cfg,
        conn_status=status,
        on_standby_change=standby_calls.append,
    )
    # What start() would do, minus the thread.
    now = time.monotonic()
    sup._start_mono = now
    sup._last_active_mono = now
    sup._next_try_mono = now
    return sup, connection, protocol, poller, health, status, standby_calls


def _backdate_idle(sup, seconds=999.0):
    sup._start_mono -= seconds
    sup._last_active_mono -= seconds


# --- connect / reconnect ---------------------------------------------------


def test_connect_success_goes_active():
    sup, conn, proto, poller, health, status, _ = _make_sup()
    sup._tick()
    assert sup._state == _State.ACTIVE
    assert status.last() == "connected"
    poller.start.assert_called_once()
    assert health.last_alive > 0.0


def test_connect_failure_backs_off():
    sup, conn, proto, poller, health, status, _ = _make_sup()
    conn.connect.side_effect = OSError("no dongle")
    sup._tick()
    assert sup._state == _State.CONNECTING
    assert status.last() == "failed"
    poller.start.assert_not_called()
    assert sup._next_try_mono > time.monotonic()
    # Next tick is inside the backoff window → no second attempt.
    sup._tick()
    assert conn.connect.call_count == 1


def test_dead_link_triggers_reconnect():
    sup, conn, proto, poller, health, status, _ = _make_sup()
    sup._tick()
    assert sup._state == _State.ACTIVE
    # Adapter silent past link_dead_s (dongle unplugged / BT drop).
    health.last_alive = time.monotonic() - 99.0
    sup._tick()
    assert sup._state == _State.CONNECTING
    assert status.last() == "connecting"
    poller.stop.assert_called()
    conn.disconnect.assert_called()
    # And it retries immediately on the next tick.
    sup._tick()
    assert conn.connect.call_count == 2


# --- standby entry ---------------------------------------------------------


def test_standby_after_idle_from_active():
    sup, conn, proto, poller, health, status, standby = _make_sup()
    sup._tick()
    assert sup._state == _State.ACTIVE
    # Car answers NO DATA (no decodes), AAOS silent → nothing bumps activity.
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    sup._tick()
    assert sup._state == _State.STANDBY
    assert status.last() == "standby"
    assert standby == [True]
    poller.stop.assert_called()
    conn.disconnect.assert_called()


def test_standby_from_connecting_stops_retrying():
    """Dongle missing AND car left → stop hammering BT so the car can sleep."""
    sup, conn, proto, poller, health, status, standby = _make_sup()
    conn.connect.side_effect = OSError("no dongle")
    sup._tick()
    _backdate_idle(sup)
    sup._tick()
    assert sup._state == _State.STANDBY
    assert standby == [True]
    attempts_before = conn.connect.call_count
    sup._tick()
    assert conn.connect.call_count == attempts_before


def test_min_uptime_blocks_standby():
    cfg = StandbyConfig(min_uptime_s=3600.0, idle_after_s=60.0)
    sup, conn, proto, poller, health, status, standby = _make_sup(cfg=cfg)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    sup._last_active_mono -= 999.0   # idle, but uptime still < min_uptime_s
    sup._tick()
    assert sup._state == _State.ACTIVE
    assert standby == []


def test_standby_disabled_never_engages():
    cfg = StandbyConfig(enabled=False, min_uptime_s=0.0, idle_after_s=60.0)
    sup, conn, proto, poller, health, status, standby = _make_sup(cfg=cfg)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    sup._tick()
    assert sup._state == _State.ACTIVE
    assert standby == []


# --- activity signals ------------------------------------------------------


def test_fresh_aaos_activity_blocks_standby():
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    # Infotainment alive and reporting drive state.
    shared.update(aaos_last_msg_ts=time.time(), ignition_state="ON")
    sup._tick()
    assert sup._state == _State.ACTIVE


def test_stale_aaos_fields_are_ignored():
    """A bridge that died mid-drive leaves speed/ignition frozen at driving
    values — they must not hold the car awake forever."""
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    shared.update(aaos_last_msg_ts=time.time() - 9999.0,
                  ignition_state="ON", speed=80, gear="D")
    sup._tick()
    assert sup._state == _State.STANDBY


def test_fresh_obd_power_blocks_standby():
    """Driving with a dead AAOS app: live decodes + real power = active."""
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    _backdate_idle(sup)
    health.mark_decode()
    shared.update(power_kw=15.0)
    sup._tick()
    assert sup._state == _State.ACTIVE


def test_stale_obd_power_is_ignored():
    """Leftover power_kw with no fresh decodes (car locked, ECUs silent)
    must not count as activity."""
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    shared.update(power_kw=15.0)
    sup._tick()
    assert sup._state == _State.STANDBY


def test_charging_blocks_standby():
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    shared.update(aaos_last_msg_ts=time.time(), is_charging=True)
    sup._tick()
    assert sup._state == _State.ACTIVE


# --- ignition ACC/OFF: immediate event-driven standby ----------------------


def test_acc_triggers_immediate_standby():
    """Lock the car → VHAL says ACC → standby NOW, no idle wait needed."""
    shared = SharedVehicleData()
    cfg = StandbyConfig(min_uptime_s=3600.0, idle_after_s=3600.0)
    sup, conn, proto, poller, health, status, standby = _make_sup(shared, cfg=cfg)
    sup._tick()
    assert sup._state == _State.ACTIVE
    shared.update(aaos_last_msg_ts=time.time(), ignition_state="ACC",
                  gear="P", speed=0)
    sup._tick()
    assert sup._state == _State.STANDBY
    assert status.last() == "standby"
    assert standby == [True]
    poller.stop.assert_called()
    conn.disconnect.assert_called()


def test_acc_immediate_standby_from_connecting():
    """ACC while the dongle is still being dialed → stop trying, go dark."""
    shared = SharedVehicleData()
    cfg = StandbyConfig(min_uptime_s=3600.0, idle_after_s=3600.0)
    sup, conn, proto, poller, health, status, standby = _make_sup(shared, cfg=cfg)
    conn.connect.side_effect = OSError("no dongle")
    sup._tick()
    shared.update(aaos_last_msg_ts=time.time(), ignition_state="ACC",
                  gear="P", speed=0)
    sup._tick()
    assert sup._state == _State.STANDBY


def test_acc_deferred_while_charging():
    """Locked + plugged in: bridge stays up so the charge screen can render."""
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    shared.update(aaos_last_msg_ts=time.time(), ignition_state="ACC",
                  gear="P", speed=0, is_charging=True)
    sup._tick()
    assert sup._state == _State.ACTIVE
    assert standby == []


def test_acc_glitch_while_moving_is_ignored():
    """A mid-drive VHAL glitch to ACC must not black out the display."""
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    shared.update(aaos_last_msg_ts=time.time(), ignition_state="ACC",
                  gear="D", speed=63)
    sup._tick()
    assert sup._state == _State.ACTIVE


def test_stale_acc_does_not_trigger_immediate_standby():
    """Frozen ACC from a long-dead bridge isn't an event — the backup idle
    timer owns that case."""
    shared = SharedVehicleData()
    cfg = StandbyConfig(min_uptime_s=3600.0, idle_after_s=3600.0)
    sup, conn, proto, poller, health, status, standby = _make_sup(shared, cfg=cfg)
    sup._tick()
    shared.update(aaos_last_msg_ts=time.time() - 9999.0, ignition_state="ACC",
                  gear="P", speed=0)
    sup._tick()
    assert sup._state == _State.ACTIVE


def test_acc_not_treated_as_activity():
    """ACC (locked, network winding down) must not reset the idle timer."""
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    # Bridge streaming at 9 Hz with frozen driving leftovers, but ACC rules.
    shared.update(aaos_last_msg_ts=time.time(), ignition_state="ACC",
                  gear="D", speed=80)
    assert not sup._activity_now(shared.snapshot(), time.monotonic())


# --- wake ------------------------------------------------------------------


def test_wake_on_aaos_return():
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    sup._tick()
    assert sup._state == _State.STANDBY

    # Driver returns: infotainment boots, app reconnects, car goes to READY.
    shared.update(aaos_last_msg_ts=time.time(), ignition_state="ON")
    sup._tick()
    assert sup._state == _State.CONNECTING
    assert standby == [True, False]
    assert status.last() == "connecting"
    # Next tick re-establishes OBD.
    sup._tick()
    assert sup._state == _State.ACTIVE
    assert status.last() == "connected"


def test_no_wake_while_car_still_parked():
    """AAOS reconnecting alone isn't enough — a locked car's ignition stays
    OFF, and the display should stay dark until real activity."""
    shared = SharedVehicleData()
    sup, conn, proto, poller, health, status, standby = _make_sup(shared)
    sup._tick()
    health.last_decode = time.monotonic() - 999.0
    _backdate_idle(sup)
    sup._tick()
    assert sup._state == _State.STANDBY

    shared.update(aaos_last_msg_ts=time.time(), ignition_state="OFF",
                  gear="P", speed=0)
    sup._tick()
    assert sup._state == _State.STANDBY
    assert standby == [True]
