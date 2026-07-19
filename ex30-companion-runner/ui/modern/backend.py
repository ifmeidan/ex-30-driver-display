"""Python↔QML bridge for the modern display.

VehicleModel pulls a SharedVehicleData snapshot on a 30 Hz QTimer and pushes
it into QML-visible properties. Setters are change-guarded so a notify (and
therefore a binding re-evaluation) only fires when a value actually moved —
the QML side owns all smoothing/animation on top of these raw updates.

ConnectionStatus is a tiny thread-safe store the OBD connector thread writes
into; the model polls it on the same tick so the loading screen can render
live connection state without any cross-thread Qt signalling.
"""

import logging
import time
from datetime import datetime

from PySide6.QtCore import Property, QObject, QTimer, Signal

from ui.trip import TripTracker

logger = logging.getLogger(__name__)

# Local-clock day window, used as the day/night fallback when AAOS is down.
DAY_START_HOUR = 7
NIGHT_START_HOUR = 19

# Sample the charging curve at most this often (seconds) while charging.
CHARGE_SAMPLE_INTERVAL_S = 2.0


class ConnectionStatus:
    """Thread-safe OBD connection state: idle → connecting → connected/failed."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._obd = "idle"

    def set_obd(self, state: str) -> None:
        with self._lock:
            self._obd = state

    def obd(self) -> str:
        with self._lock:
            return self._obd


# (qml name, qt type, default) — one QML property per row, notify-guarded.
_FIELDS = [
    # drive
    ("speed",        float, 0.0),    # km/h
    ("gear",         str,   ""),     # P/R/N/D or "" (unknown)
    ("powerKw",      float, 0.0),    # + draw / − regen
    ("brakePct",     float, 0.0),    # 0..1
    ("throttlePct",  float, 0.0),    # 0..1
    ("regenPct",     float, 0.0),    # 0..1
    ("blindLeft",    bool,  False),
    ("blindRight",   bool,  False),
    ("blinkerLeft",  bool,  False),
    ("blinkerRight", bool,  False),
    # battery / environment
    ("socPct",       float, 0.0),
    ("batteryTemp",  float, 0.0),
    ("ambientTemp",  float, 0.0),
    ("odometer",     int,   0),
    ("rangeKm",      float, -1.0),   # −1 = unknown (AAOS stale)
    ("hvVoltage",    float, 0.0),
    ("hvCurrent",    float, 0.0),
    # charging
    ("isCharging",     bool,  False),
    ("chargePowerKw",  float, 0.0),
    ("chargeHistory",  "QVariantList", []),   # [[soc%, kW], ...]
    # session energy integrals (poller-owned)
    ("consumedKwh",  float, 0.0),
    ("regenKwh",     float, 0.0),
    # trip metrics (charge-to-charge, TripTracker) — −1 = not available yet
    ("tripKm",         float, -1.0),
    ("tripKwhUsed",    float, -1.0),
    ("tripEfficiency", float, -1.0),  # kWh/100km
    ("tripRegenKwh",   float, -1.0),
    ("rangeTo10Km",    float, -1.0),
    # links / theme
    ("obdState",      str,  "idle"),  # idle/connecting/connected/failed
    ("aaosConnected", bool, False),
    ("aaosEverSeen",  bool, False),
    ("isDay",         bool, True),
]


def _build_base():
    """Build a QObject subclass with a notify-guarded Property per field."""
    ns = {}
    for name, qt_type, _default in _FIELDS:
        signal = Signal()
        ns[name + "Changed"] = signal

        def _getter(self, _n=name):
            return self._values[_n]

        ns[name] = Property(qt_type, _getter, notify=signal)
    return type("_VehicleModelBase", (QObject,), ns)


class VehicleModel(_build_base()):
    """Owns the UI-side tick: snapshot → trip metrics → charge curve → QML."""

    def __init__(self, shared_data, conn_status=None, *,
                 trip_state_path: str = "state/trip.json",
                 fps: int = 30, parent=None):
        super().__init__(parent)
        self._values = {name: default for name, _t, default in _FIELDS}
        self._shared = shared_data
        self._conn = conn_status
        self._trip = TripTracker(trip_state_path)

        # Charge-curve sampler state.
        self._charge_history: list[tuple[float, float]] = []
        self._was_in_charge = False
        self._last_charge_sample_t = 0.0

        # Demo/test override: None = automatic (AAOS night_mode, clock fallback).
        self.force_day: bool | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000 // fps)

    # --- internals ---------------------------------------------------------

    def _set(self, name, value):
        if self._values[name] != value:
            self._values[name] = value
            getattr(self, name + "Changed").emit()

    def _sample_charge_history(self, d) -> list:
        """Seed from shared charge_history on each charge-start transition,
        then append a (soc, |kW|) sample at most every CHARGE_SAMPLE_INTERVAL_S."""
        if d.is_charging and not self._was_in_charge:
            self._charge_history = list(d.charge_history)
            self._last_charge_sample_t = 0.0
        self._was_in_charge = d.is_charging

        if d.is_charging and d.soc > 0:
            now = time.monotonic()
            if now - self._last_charge_sample_t >= CHARGE_SAMPLE_INTERVAL_S:
                self._charge_history.append(
                    (float(d.soc), abs(float(d.charge_power_kw)))
                )
                self._last_charge_sample_t = now

        return self._charge_history if self._charge_history else d.charge_history

    def _is_day(self, d) -> bool:
        if self.force_day is not None:
            return self.force_day
        if not d.aaos_stale:
            return not d.night_mode
        h = datetime.now().hour
        return DAY_START_HOUR <= h < NIGHT_START_HOUR

    def _tick(self):
        d = self._shared.snapshot()

        trip = self._trip.update(
            odometer=d.odometer,
            soc=d.soc,
            regen_kwh=d.regen_kwh,
            is_charging=d.is_charging,
            range_m_aaos=d.range_m_aaos,
            aaos_stale=d.aaos_stale,
            pack_capacity_wh=d.pack_capacity_wh,
        )
        history = self._sample_charge_history(d)

        self._set("speed", float(d.speed))
        self._set("gear", d.gear or "")
        self._set("powerKw", float(d.power_kw))
        self._set("brakePct", max(0.0, min(1.0, float(d.brake_pct))))
        self._set("throttlePct", max(0.0, min(1.0, float(d.throttle_pct))))
        self._set("regenPct", max(0.0, min(1.0, float(d.regen_pct))))
        self._set("blindLeft", bool(d.blind_spot_left))
        self._set("blindRight", bool(d.blind_spot_right))
        self._set("blinkerLeft", bool(d.blinker_left))
        self._set("blinkerRight", bool(d.blinker_right))

        self._set("socPct", float(d.soc))
        self._set("batteryTemp", float(d.battery_temp))
        self._set("ambientTemp", float(d.ambient_temp))
        self._set("odometer", int(d.odometer))
        self._set("rangeKm",
                  d.range_m_aaos / 1000.0
                  if (not d.aaos_stale and d.range_m_aaos > 0) else -1.0)
        self._set("hvVoltage", float(d.hv_voltage))
        self._set("hvCurrent", float(d.hv_current))

        self._set("isCharging", bool(d.is_charging))
        self._set("chargePowerKw", float(d.charge_power_kw))

        old = self._values["chargeHistory"]
        if len(old) != len(history) or (history and old and old[-1] != list(history[-1])):
            self._set("chargeHistory", [[float(s), float(k)] for s, k in history])

        self._set("consumedKwh", float(d.consumed_kwh))
        self._set("regenKwh", float(d.regen_kwh))

        def _f(v):
            return float(v) if v is not None else -1.0
        self._set("tripKm", _f(trip.km))
        self._set("tripKwhUsed", _f(trip.kwh_used))
        self._set("tripEfficiency", _f(trip.efficiency_kwh_per_100km))
        self._set("tripRegenKwh", _f(trip.regen_kwh))
        self._set("rangeTo10Km", _f(trip.range_to_10_km))

        self._set("obdState", self._conn.obd() if self._conn else "idle")
        self._set("aaosConnected", not d.aaos_stale)
        self._set("aaosEverSeen", d.aaos_last_msg_ts > 0)
        self._set("isDay", self._is_day(d))
