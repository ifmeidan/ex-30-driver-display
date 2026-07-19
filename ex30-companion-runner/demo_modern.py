"""Demo bench for the MODERN display — mock data, no OBD2/AAOS hardware.

Usage:
    .venv/bin/python demo_modern.py                # full boot → loading → dash
    .venv/bin/python demo_modern.py --skip-boot    # straight to the dash
    .venv/bin/python demo_modern.py --charge       # start on charging preset
    .venv/bin/python demo_modern.py --rotate       # portrait window (Pi format)
    .venv/bin/python demo_modern.py --night        # force night theme
    .venv/bin/python demo_modern.py --obd-fail     # simulate dead OBD adapter
    .venv/bin/python demo_modern.py --aaos-fail    # silent AAOS bridge (30s loading timeout, speed shows –)
    .venv/bin/python demo_modern.py --capture DIR  # scripted screenshot run

Hotkeys:
    1-6    drive presets (cruise/accel/regen/park/low-bat/reverse)
    7-8    charging presets (slow/fast)  ·  9 stress  ·  0 hard braking
    T/Y/U  trip presets  ·  X clear trip baseline
    C / D  switch charging / drive
    L / R  toggle blind spot left/right  ·  B / N blinker left/right
    A      toggle auto-drive simulation (on by default)
    F      cycle theme force: auto → day → night
    Space  skip boot/loading phase  ·  Esc quit
"""

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QObject, QTimer, Qt, Slot
from PySide6.QtGui import QGuiApplication

from config.display import DisplayConfig
from shared.vehicle_data import SharedVehicleData
from ui.modern.app import ModernDisplayApp
from ui.modern.backend import ConnectionStatus

_AAOS_FRESH = {
    "aaos_stale": False,
    "pack_capacity_wh": 64000.0,
}

PRESETS = {
    1: {  # Normal cruising
        "speed": 82, "soc": 72.4, "battery_temp": 28.0,
        "ambient_temp": 19.0, "odometer": 2632, "power_kw": 14.5,
        "throttle_pct": 0.15, "regen_pct": 0.0, "brake_pct": 0.0,
        "gear": "D", "hv_voltage": 408.0, "hv_current": 35.5,
        "consumed_kwh": 3.21, "regen_kwh": 0.84,
        "blind_spot_left": False, "blind_spot_right": False,
        "blinker_left": False, "blinker_right": False,
        "is_charging": False,
        "range_m_aaos": 312_000.0, **_AAOS_FRESH,
    },
    2: {  # Hard acceleration
        "speed": 127, "soc": 65.0, "battery_temp": 34.0,
        "ambient_temp": 22.0, "odometer": 2632, "power_kw": 86.0,
        "throttle_pct": 0.86, "regen_pct": 0.0, "brake_pct": 0.0,
        "gear": "D", "is_charging": False,
        "consumed_kwh": 5.44, "regen_kwh": 1.12,
        "range_m_aaos": 245_000.0, **_AAOS_FRESH,
    },
    3: {  # Regen braking
        "speed": 54, "soc": 71.8, "battery_temp": 30.0,
        "ambient_temp": 19.0, "odometer": 2632, "power_kw": -25.0,
        "throttle_pct": 0.0, "regen_pct": 0.25, "brake_pct": 0.0,
        "gear": "D", "is_charging": False,
        "consumed_kwh": 3.21, "regen_kwh": 0.92,
        "range_m_aaos": 308_000.0, **_AAOS_FRESH,
    },
    4: {  # Parked
        "speed": 0, "soc": 85.0, "battery_temp": 24.0,
        "ambient_temp": 16.0, "odometer": 2632, "power_kw": 0.0,
        "throttle_pct": 0.0, "regen_pct": 0.0, "brake_pct": 0.0,
        "gear": "P", "is_charging": False,
        "consumed_kwh": 0.0, "regen_kwh": 0.0,
        "range_m_aaos": 380_000.0, **_AAOS_FRESH,
    },
    5: {  # Low battery
        "speed": 45, "soc": 8.2, "battery_temp": 22.0,
        "ambient_temp": 5.0, "odometer": 2632, "power_kw": 10.0,
        "throttle_pct": 0.1, "regen_pct": 0.0, "brake_pct": 0.0,
        "gear": "D", "is_charging": False,
        "consumed_kwh": 12.5, "regen_kwh": 3.1,
        "range_m_aaos": 28_000.0, **_AAOS_FRESH,
    },
    6: {  # Reverse
        "speed": 5, "soc": 90.0, "battery_temp": 26.0,
        "ambient_temp": 20.0, "odometer": 2632, "power_kw": 3.0,
        "throttle_pct": 0.03, "regen_pct": 0.0, "brake_pct": 0.0,
        "gear": "R", "is_charging": False,
        "consumed_kwh": 0.01, "regen_kwh": 0.0,
        "range_m_aaos": 400_000.0, **_AAOS_FRESH,
    },
    7: {  # Charging — DC tapering
        "is_charging": True, "soc": 49.3, "battery_temp": 32.0,
        "ambient_temp": 14.0, "odometer": 2632,
        "charge_power_kw": 48.5,
        "charge_history": [(10, 148), (20, 150), (30, 145), (40, 130), (49.3, 48.5)],
        "speed": 0, "gear": "P", "power_kw": 0.0,
        "throttle_pct": 0.0, "regen_pct": 0.0, "brake_pct": 0.0,
        "blind_spot_left": False, "blind_spot_right": False,
        "blinker_left": False, "blinker_right": False,
        **_AAOS_FRESH,
    },
    8: {  # Charging — peak rate
        "is_charging": True, "soc": 22.0, "battery_temp": 28.0,
        "ambient_temp": 18.0, "odometer": 2632,
        "charge_power_kw": 153.0,
        "charge_history": [(5, 80), (10, 130), (15, 148), (20, 152), (22, 153)],
        "speed": 0, "gear": "P", "power_kw": 0.0,
        "throttle_pct": 0.0, "regen_pct": 0.0, "brake_pct": 0.0,
        "blind_spot_left": False, "blind_spot_right": False,
        "blinker_left": False, "blinker_right": False,
        **_AAOS_FRESH,
    },
    9: {  # Stress test
        "speed": 180, "soc": 100.0, "battery_temp": 55.0,
        "ambient_temp": 40.0, "odometer": 99999, "power_kw": 100.0,
        "throttle_pct": 1.0, "regen_pct": 0.0, "brake_pct": 1.0,
        "gear": "D", "is_charging": False,
        "consumed_kwh": 99.99, "regen_kwh": 99.9,
        "blind_spot_left": True, "blind_spot_right": True,
        "blinker_left": True, "blinker_right": True,
        "range_m_aaos": 420_000.0, **_AAOS_FRESH,
    },
    0: {  # Hard braking
        "speed": 38, "soc": 70.0, "battery_temp": 31.0,
        "ambient_temp": 18.0, "odometer": 2632, "power_kw": -45.0,
        "throttle_pct": 0.0, "regen_pct": 0.45, "brake_pct": 0.72,
        "gear": "D", "is_charging": False,
        "consumed_kwh": 4.10, "regen_kwh": 1.55,
        "range_m_aaos": 290_000.0, **_AAOS_FRESH,
    },
}

TRIP_PRESETS = {
    "T": {
        "is_charging": False, "speed": 0, "gear": "P",
        "soc": 100.0, "odometer": 10000, "regen_kwh": 0.0,
        "consumed_kwh": 0.0, "power_kw": 0.0,
        "battery_temp": 24.0, "ambient_temp": 19.0,
        "range_m_aaos": 480_000.0, **_AAOS_FRESH,
    },
    "Y": {
        "is_charging": False, "speed": 72, "gear": "D",
        "soc": 87.5, "odometer": 10050, "regen_kwh": 1.6,
        "consumed_kwh": 8.4, "power_kw": 18.0,
        "throttle_pct": 0.18, "regen_pct": 0.0,
        "battery_temp": 28.0, "ambient_temp": 19.0,
        "range_m_aaos": 408_000.0, **_AAOS_FRESH,
    },
    "U": {
        "is_charging": False, "speed": 95, "gear": "D",
        "soc": 55.0, "odometer": 10200, "regen_kwh": 5.8,
        "consumed_kwh": 28.9, "power_kw": 22.0,
        "throttle_pct": 0.22, "regen_pct": 0.0,
        "battery_temp": 32.0, "ambient_temp": 21.0,
        "range_m_aaos": 240_000.0, **_AAOS_FRESH,
    },
}


class DriveSim:
    """Smooth synthetic drive: speed/power/brake/blinkers evolve over time
    so the animation quality can be judged with continuous motion."""

    def __init__(self, shared: SharedVehicleData):
        self._shared = shared
        self._t0 = time.monotonic()
        self._prev_v = 0.0
        self._prev_t = self._t0
        self._soc = 72.4
        self._consumed = 3.2
        self._regen = 0.84
        self._dist_km = 0.0

    def tick(self):
        now = time.monotonic()
        t = now - self._t0
        dt = max(1e-3, now - self._prev_t)
        self._prev_t = now

        v = max(0.0, 55 + 45 * math.sin(t / 11) + 12 * math.sin(t / 3.1))
        accel = (v - self._prev_v) / dt          # km/h per s
        self._prev_v = v

        p = v * 0.16 + accel * v * 0.055
        p = max(-60.0, min(140.0, p))
        throttle = max(0.0, min(1.0, p / 120)) if p > 0 else 0.0
        regen = max(0.0, min(1.0, -p / 60)) if p < 0 else 0.0
        brake = max(0.0, min(1.0, (-accel - 2.0) / 4.0)) if accel < -2.0 else 0.0

        self._consumed += max(0.0, p) * dt / 3600
        self._regen += max(0.0, -p) * dt / 3600
        self._soc = max(5.0, self._soc - max(0.0, p) * dt / 3600 / 64.0 * 100)
        self._dist_km += v * dt / 3600

        blinker_l = (t % 37) < 4 and v > 10
        blinker_r = 20 <= (t % 53) < 24 and v > 10
        blind_l = 5 <= (t % 23) < 11
        blind_r = 14 <= (t % 31) < 21

        self._shared.update(
            speed=int(round(v)),
            gear="D" if v > 0.5 else "P",
            power_kw=round(p, 1),
            throttle_pct=throttle,
            regen_pct=regen,
            brake_pct=brake,
            soc=round(self._soc, 1),
            battery_temp=28.0 + 3 * math.sin(t / 60),
            ambient_temp=19.0,
            odometer=2632 + int(self._dist_km),
            consumed_kwh=round(self._consumed, 2),
            regen_kwh=round(self._regen, 2),
            blinker_left=blinker_l,
            blinker_right=blinker_r,
            blind_spot_left=blind_l,
            blind_spot_right=blind_r,
            is_charging=False,
            range_m_aaos=self._soc * 3880.0,
        )


class BenchController(QObject):
    """Receives key presses forwarded from QML."""

    def __init__(self, shared: SharedVehicleData):
        super().__init__()
        self._shared = shared
        self.model = None          # attached after window construction
        self.sim_enabled = True
        self._theme_force = 0      # 0 auto, 1 day, 2 night

    def _toggle(self, field):
        self._shared.update(**{field: not self._shared.get(field)})

    @Slot(int, str)
    def key(self, key, _text):
        if key == Qt.Key.Key_Escape:
            QGuiApplication.instance().quit()
            return
        if key == Qt.Key.Key_A:
            self.sim_enabled = not self.sim_enabled
            print(f"auto-drive sim {'ON' if self.sim_enabled else 'OFF'}")
            return
        if key == Qt.Key.Key_F:
            self._theme_force = (self._theme_force + 1) % 3
            force = {0: None, 1: True, 2: False}[self._theme_force]
            if self.model is not None:
                self.model.force_day = force
            print(f"theme force: {({0: 'auto', 1: 'day', 2: 'night'})[self._theme_force]}")
            return
        if key == Qt.Key.Key_C:
            self.sim_enabled = False
            self._shared.update(**PRESETS[7])
            return
        if key == Qt.Key.Key_D:
            self.sim_enabled = False
            self._shared.update(**PRESETS[1])
            return
        if key == Qt.Key.Key_L:
            self._toggle("blind_spot_left"); return
        if key == Qt.Key.Key_R:
            self._toggle("blind_spot_right"); return
        if key == Qt.Key.Key_B:
            self._toggle("blinker_left"); return
        if key == Qt.Key.Key_N:
            self._toggle("blinker_right"); return
        if key == Qt.Key.Key_X:
            if self.model is not None:
                self._clear_trip(self.model._trip)
                print("trip baseline cleared")
            return
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            num = key - Qt.Key.Key_0
            if num in PRESETS:
                self.sim_enabled = False
                self._shared.update(**PRESETS[num])
                print(f"preset {num} loaded")
            return
        name = {Qt.Key.Key_T: "T", Qt.Key.Key_Y: "Y", Qt.Key.Key_U: "U"}.get(key)
        if name:
            self.sim_enabled = False
            if name == "T" and self.model is not None:
                self._clear_trip(self.model._trip)
            self._shared.update(**TRIP_PRESETS[name])
            print(f"trip preset {name} loaded")

    @staticmethod
    def _clear_trip(tracker):
        tracker._start_odo = None
        tracker._start_soc = None
        tracker._trip_regen_kwh = 0.0
        tracker._session_regen_prev = None
        tracker._save()


def tick_charge_sim(shared: SharedVehicleData):
    """Advance SoC + taper power once per second while charging."""
    snap = shared.snapshot()
    if not snap.is_charging:
        return
    new_soc = min(100.0, snap.soc + 1.2)
    if new_soc < 40:
        kw = 150.0
    elif new_soc < 60:
        kw = 150.0 - (new_soc - 40) * 2.5
    elif new_soc < 80:
        kw = 100.0 - (new_soc - 60) * 3.0
    else:
        kw = max(15.0, 40.0 - (new_soc - 80) * 1.25)
    shared.update(soc=new_soc, charge_power_kw=kw)


def main() -> int:
    parser = argparse.ArgumentParser(description="EX30 Modern Display Demo")
    parser.add_argument("--rotate", action="store_true",
                        help="Portrait window with rotated canvas (Pi format)")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--charge", action="store_true",
                        help="Start on the charging preset")
    parser.add_argument("--skip-boot", action="store_true",
                        help="Skip boot animation + loading, straight to dash")
    parser.add_argument("--day", action="store_true", help="Force day theme")
    parser.add_argument("--night", action="store_true", help="Force night theme")
    parser.add_argument("--obd-delay", type=float, default=2.0,
                        help="Seconds until mock OBD connects")
    parser.add_argument("--aaos-delay", type=float, default=3.2,
                        help="Seconds until mock AAOS bridge connects")
    parser.add_argument("--obd-fail", action="store_true",
                        help="OBD connect fails (loading shows OFFLINE)")
    parser.add_argument("--aaos-fail", action="store_true",
                        help="AAOS never connects (30s loading timeout)")
    parser.add_argument("--capture", metavar="DIR", default=None,
                        help="Scripted run: save screenshots to DIR and exit")
    args = parser.parse_args()

    app = QGuiApplication(sys.argv)

    shared = SharedVehicleData()
    shared.update(pack_capacity_wh=64000.0)
    conn = ConnectionStatus()
    controller = BenchController(shared)

    os.makedirs("state", exist_ok=True)
    window = ModernDisplayApp(
        DisplayConfig(fullscreen=args.fullscreen),
        shared, conn,
        rotated=args.rotate,
        skip_boot=args.skip_boot,
        bench=controller,
        trip_state_path="state/trip_demo.json",
    )
    controller.model = window.model
    if args.day:
        window.model.force_day = True
        controller._theme_force = 1
    elif args.night:
        window.model.force_day = False
        controller._theme_force = 2

    if not args.fullscreen:
        window.resize(240, 960) if args.rotate else window.resize(1440, 360)

    # ── Mock connection timeline ─────────────────────────────────
    obd_delay = args.obd_delay
    aaos_delay = args.aaos_delay
    if args.capture:
        # Fixed timeline so the scripted screenshots land on known states
        obd_delay, aaos_delay = 3.4, 6.4

    conn.set_obd("connecting")
    if args.obd_fail:
        QTimer.singleShot(int(obd_delay * 1000) + 1200,
                          lambda: conn.set_obd("failed"))
    else:
        QTimer.singleShot(int(obd_delay * 1000),
                          lambda: conn.set_obd("connected"))
    if not args.aaos_fail:
        QTimer.singleShot(int(aaos_delay * 1000),
                          lambda: shared.update(
                              aaos_stale=False,
                              aaos_last_msg_ts=time.time(),
                              night_mode=False,
                              pack_capacity_wh=64000.0))

    # ── Simulators ───────────────────────────────────────────────
    if args.charge:
        controller.sim_enabled = False
        shared.update(**PRESETS[7])
    else:
        shared.update(**PRESETS[4])   # parked until the sim takes over

    sim = DriveSim(shared)
    sim_timer = QTimer()
    sim_timer.timeout.connect(
        lambda: sim.tick() if (controller.sim_enabled
                               and not shared.get("is_charging")) else None)
    sim_timer.start(50)

    charge_timer = QTimer()
    charge_timer.timeout.connect(lambda: tick_charge_sim(shared))
    charge_timer.start(1000)

    # ── Scripted capture mode ────────────────────────────────────
    if args.capture:
        os.makedirs(args.capture, exist_ok=True)
        controller.sim_enabled = False
        shared.update(**PRESETS[4])

        def grab(name):
            img = window.grabWindow()
            path = os.path.join(args.capture, name + ".png")
            img.save(path)
            print(f"captured {path}")

        def load(preset, **extra):
            controller.sim_enabled = False
            shared.update(**{**PRESETS[preset], **extra})

        steps = [
            (1200, lambda: grab("1-boot-mark")),
            (3800, lambda: grab("2-boot-wordmark")),
            (5800, lambda: grab("3-loading")),
            (8300, lambda: (setattr(window.model, "force_day", True),
                            load(1))),
            (8900, lambda: grab("4-drive-day")),
            (9000, lambda: (setattr(window.model, "force_day", False),
                            load(0, blinker_left=True, blind_spot_right=True))),
            (9950, lambda: grab("5-drive-night-braking")),
            (10300, lambda: load(8)),
            (13400, lambda: grab("6-charging-night")),
            (13800, app.quit),
        ]
        for ms, fn in steps:
            QTimer.singleShot(ms, fn)

    window.show()

    if not args.capture:
        print(__doc__)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
