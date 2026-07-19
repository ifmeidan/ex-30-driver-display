"""OBD connection supervisor: connect/reconnect, link health, parking standby.

Why this exists : the poller used to run unconditionally for as
long as the Pi had power. Parked, that meant a UDS read on the diagnostic
CAN every 0.5s — enough bus activity to keep the vehicle's network awake,
which keeps the 12V system (and the USB-C port powering this Pi) alive,
which keeps the poller polling. The car sometimes never slept and burned
battery overnight. The supervisor breaks that loop.

States:
  CONNECTING — trying to open the serial port + init the ELM327, with
               capped exponential backoff between attempts.
  ACTIVE     — poller running. Watches LinkHealth: if the adapter stops
               returning bytes (dongle unplugged, BT drop) → back to
               CONNECTING. NO DATA still counts as adapter-alive; only
               silence/exceptions mean the link is gone.
  STANDBY    — car judged "left": poller stopped, serial closed so the
               RFCOMM link drops and the vLinker's own auto-sleep can kick
               in, CAN goes quiet, the car is free to sleep and cut our
               power. Optionally blanks the screen via on_standby_change.

Standby entry, fastest first:
  1. Immediate, event-driven: ignition ACC/OFF/LOCK with the car parked
     (gear P, speed 0, not charging) — on the EX30's retail VHAL, ACC fires
     the moment the car is locked, so the screen goes dark right away.
  2. Backup idle timer: `idle_after_s` with no activity signal at all —
     covers a dead bridge, missed transitions, and every VHAL surprise.

Activity signals (any one of these resets the idle timer):
  From AAOS (only while the bridge has spoken within `aaos_gone_s`, and
  ignored — except charging — while ignition reports a parked state):
    ignition ON/START, gear not P, speed > 0, charge port connected.
  From OBD (only while PIDs decoded within the last DECODE_FRESH_S — a
  locked car answers NO DATA, so fresh decodes mean it is genuinely up):
    |power_kw| ≥ active_power_kw, or brake pedal pressed.

Wake from standby is AAOS-driven: the driver opens the car, the VHAL flips
ignition to ON, the companion app (re)connects and the frame lands here.
A Pi power cycle (normal case: the sleeping car cut USB power, driver
returns, port re-energizes, Pi boots) also starts fresh.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum, auto
from typing import Callable, Optional

from config.settings import StandbyConfig
from obd2.connection import OBD2Connection
from obd2.protocol import ELMProtocol
from shared.vehicle_data import SharedVehicleData

logger = logging.getLogger(__name__)

# How recently a PID must have decoded for OBD-derived values (power_kw,
# brake_pct) to be trusted as live rather than stale leftovers.
DECODE_FRESH_S = 10.0

# EX30 retail VHAL ignition semantics (verified 2026-07-17 by correlating
# lock/unlock times against the bridge JSONL):
#   ON    — car unlocked/opened; someone is there. Reported even before READY.
#   ACC   — car LOCKED but the vehicle network still awake (wind-down /
#           housekeeping). NOT "accessory mode with a driver inside".
#   OFF/LOCK — never delivered; the head unit suspends before sending them.
# So ON/START mean presence, and ACC is an explicit "driver left" edge that
# standby acts on immediately.
IGNITION_ACTIVE = frozenset({"ON", "START"})
IGNITION_PARKED = frozenset({"ACC", "OFF", "LOCK"})

# Reconnect backoff: attempt n waits BACKOFF_S[min(n, len-1)] seconds.
BACKOFF_S = (2.0, 5.0, 10.0, 20.0, 30.0)


class LinkHealth:
    """Timestamps stamped by the poller thread, read by the supervisor.

    Plain float attributes on purpose — assignments are atomic under the
    GIL and both sides only ever read-or-overwrite whole values.
    """

    def __init__(self) -> None:
        self.last_alive = 0.0    # monotonic: any bytes back from the adapter
        self.last_decode = 0.0   # monotonic: a PID actually decoded

    def mark_alive(self) -> None:
        self.last_alive = time.monotonic()

    def mark_decode(self) -> None:
        self.last_decode = time.monotonic()

    def reset(self) -> None:
        """Grace-stamp both clocks (call when a connection comes up)."""
        now = time.monotonic()
        self.last_alive = now
        self.last_decode = now


class _State(Enum):
    CONNECTING = auto()
    ACTIVE = auto()
    STANDBY = auto()


class ObdSupervisor:
    """Owns the OBD connect/health/standby lifecycle on its own thread.

    `conn_status` (ui.modern.backend.ConnectionStatus) is optional — headless
    tests construct the supervisor without one. `on_standby_change(bool)` is
    invoked from the supervisor thread on standby enter/exit; keep it Qt-free.
    """

    TICK_S = 1.0

    def __init__(
        self,
        connection: OBD2Connection,
        protocol: ELMProtocol,
        poller,
        shared: SharedVehicleData,
        health: LinkHealth,
        config: StandbyConfig,
        conn_status=None,
        on_standby_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self._connection = connection
        self._protocol = protocol
        self._poller = poller
        self._shared = shared
        self._health = health
        self._cfg = config
        self._conn_status = conn_status
        self._on_standby_change = on_standby_change

        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._state = _State.CONNECTING
        self._attempts = 0
        self._next_try_mono = 0.0
        self._start_mono = 0.0
        self._last_active_mono = 0.0
        self._last_ignition = ""

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._start_mono = time.monotonic()
        self._last_active_mono = self._start_mono
        self._next_try_mono = self._start_mono
        self._set_status("connecting")
        self._thread = threading.Thread(
            target=self._loop, name="obd-supervisor", daemon=True
        )
        self._thread.start()
        logger.info(
            "OBD supervisor started (standby %s, idle_after=%.0fs)",
            "enabled" if self._cfg.enabled else "DISABLED",
            self._cfg.idle_after_s,
        )

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        self._teardown_obd()

    # --- status helpers ----------------------------------------------------

    def _set_status(self, state: str) -> None:
        if self._conn_status is not None:
            self._conn_status.set_obd(state)

    def _teardown_obd(self) -> None:
        try:
            self._poller.stop()
        except Exception:
            logger.exception("poller stop failed")
        try:
            self._connection.disconnect()
        except Exception:
            logger.exception("serial disconnect failed")

    # --- main loop ---------------------------------------------------------

    def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:
                logger.exception("supervisor tick failed")
            if self._stop_evt.wait(self.TICK_S):
                return

    def _tick(self) -> None:
        now_mono = time.monotonic()
        snap = self._shared.snapshot()

        if snap.ignition_state != self._last_ignition:
            # INFO on purpose: correlating these against real lock/unlock
            # times is how the VHAL semantics were established — keep the
            # audit trail cheap to collect.
            logger.info("ignition_state: %r → %r",
                        self._last_ignition, snap.ignition_state)
            self._last_ignition = snap.ignition_state

        if self._activity_now(snap, now_mono):
            self._last_active_mono = now_mono

        standby_due = (
            self._cfg.enabled
            and now_mono - self._start_mono >= self._cfg.min_uptime_s
            and now_mono - self._last_active_mono >= self._cfg.idle_after_s
        )
        # Explicit driver-left edge: the car said ACC (locked) while parked —
        # no idle wait, no min-uptime grace. Guards: AAOS must be talking,
        # the car must actually look parked (a mid-drive VHAL glitch to ACC
        # with speed/gear still live must not black out the display), and
        # charging keeps the bridge/screen up for the charge curve.
        standby_now = (
            self._cfg.enabled
            and not self._aaos_gone(snap)
            and snap.ignition_state in IGNITION_PARKED
            and snap.gear in ("P", "")
            and snap.speed == 0
            and not snap.is_charging
        )

        if self._state == _State.ACTIVE:
            if standby_now:
                self._enter_standby("ignition %s — driver left" % snap.ignition_state)
            elif now_mono - self._health.last_alive > self._cfg.link_dead_s:
                logger.warning(
                    "OBD link dead — no adapter bytes for %.0fs; reconnecting",
                    now_mono - self._health.last_alive,
                )
                self._teardown_obd()
                self._set_status("connecting")
                self._attempts = 0
                self._next_try_mono = time.monotonic()
                self._state = _State.CONNECTING
            elif standby_due:
                self._enter_standby(
                    "no activity for %.0fs (car looks left)"
                    % (now_mono - self._last_active_mono))

        elif self._state == _State.CONNECTING:
            if standby_now:
                self._enter_standby("ignition %s — driver left" % snap.ignition_state)
            elif standby_due:
                self._enter_standby(
                    "no activity for %.0fs (car looks left)"
                    % (now_mono - self._last_active_mono))
            elif time.monotonic() >= self._next_try_mono:
                self._set_status("connecting")
                if self._attempt_connect():
                    self._state = _State.ACTIVE
                else:
                    delay = BACKOFF_S[min(self._attempts, len(BACKOFF_S) - 1)]
                    self._attempts += 1
                    self._next_try_mono = time.monotonic() + delay
                    self._set_status("failed")

        elif self._state == _State.STANDBY:
            if self._last_active_mono == now_mono:
                self._exit_standby()

    # --- state transitions -------------------------------------------------

    def _attempt_connect(self) -> bool:
        try:
            self._connection.connect()
            if self._protocol.initialize():
                self._health.reset()
                self._poller.start()
                self._set_status("connected")
                logger.info("OBD2 polling started")
                return True
            logger.error("ELM327 init failed — will retry")
            self._connection.disconnect()
        except Exception as exc:
            logger.warning("OBD connect attempt failed: %s", exc)
            try:
                self._connection.disconnect()
            except Exception:
                pass
        return False

    def _enter_standby(self, reason: str) -> None:
        logger.info(
            "entering standby — %s; stopping OBD polling and dropping the "
            "BT link so the car can sleep", reason,
        )
        self._teardown_obd()
        self._set_status("standby")
        self._state = _State.STANDBY
        self._notify_standby(True)

    def _exit_standby(self) -> None:
        logger.info("waking from standby — activity detected")
        self._notify_standby(False)
        self._set_status("connecting")
        self._attempts = 0
        self._next_try_mono = time.monotonic()
        self._state = _State.CONNECTING

    def _notify_standby(self, standby: bool) -> None:
        if self._on_standby_change is None:
            return
        try:
            self._on_standby_change(standby)
        except Exception:
            logger.exception("standby-change callback failed")

    # --- activity detection ------------------------------------------------

    def _aaos_gone(self, snap) -> bool:
        """Bridge silent long enough that its snapshot fields are frozen
        leftovers, not live truth."""
        return (
            snap.aaos_last_msg_ts == 0.0
            or time.time() - snap.aaos_last_msg_ts > self._cfg.aaos_gone_s
        )

    def _activity_now(self, snap, now_mono: float) -> bool:
        """True if anything indicates the car is in use right now."""
        cfg = self._cfg

        # AAOS-derived signals, only while the bridge has spoken recently —
        # a bridge that died at 80 km/h must not hold us active forever.
        if not self._aaos_gone(snap):
            if snap.is_charging:
                return True
            if snap.ignition_state in IGNITION_PARKED:
                # Explicit driver-left signal wins over frozen gear/speed
                # leftovers. OBD-side checks below still apply as a net.
                pass
            else:
                if snap.ignition_state in IGNITION_ACTIVE:
                    return True
                if snap.gear not in ("P", ""):
                    return True
                if snap.speed > 0:
                    return True

        # OBD-derived signals, only while PIDs actually decode: a locked car
        # answers NO DATA, so fresh decodes mean the ECUs are genuinely up.
        if now_mono - self._health.last_decode <= DECODE_FRESH_S:
            if abs(snap.power_kw) >= cfg.active_power_kw:
                return True
            if snap.brake_pct >= cfg.active_brake_pct:
                return True

        return False
