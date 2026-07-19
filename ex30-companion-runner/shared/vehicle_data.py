"""Shared vehicle data model — written by the poller, read by the UI.

Thread safety: all access goes through snapshot() / update() which hold
a lightweight threading.Lock.  The poller writes individual fields via
update(); the UI grabs an immutable snapshot on each paint frame.
"""

import threading
import copy
from dataclasses import dataclass, field


@dataclass
class VehicleData:
    """All vehicle state consumed by the display."""

    # ── Shared ─────────────────────────────────────────────
    battery_temp: float = 0.0       # °C  (HV battery)
    soc: float = 0.0                # %   (display SoC)
    hv_soc: float = 0.0             # %   (raw BMS SoC)
    ambient_temp: float = 0.0       # °C
    odometer: int = 0               # km
    is_charging: bool = False

    # ── Drive ──────────────────────────────────────────────
    speed: int = 0                  # km/h
    gear: str = ''                  # R / P / N / D / '' (empty = unknown)
    brake_pct: float = 0.0          # 0.0–1.0
    throttle_pct: float = 0.0       # 0.0–1.0  (derived from power_kw)
    regen_pct: float = 0.0          # 0.0–1.0  (derived from power_kw)
    power_kw: float = 0.0           # kW  (positive = draw, negative = regen)
    consumed_kwh: float = 0.0       # trip kWh consumed (integrated)
    regen_kwh: float = 0.0          # trip kWh recovered (integrated)
    blind_spot_left: bool = False
    blind_spot_right: bool = False
    blinker_left: bool = False
    blinker_right: bool = False

    # ── Charging ───────────────────────────────────────────
    charge_power_kw: float = 0.0
    charge_history: list = field(default_factory=list)  # [(soc_pct, kw), ...]

    # ── HV system ──────────────────────────────────────────
    hv_voltage: float = 0.0         # V
    hv_current: float = 0.0         # A

    # ── AAOS bridge ──────────────────────────────────────
    # Populated by aaos_bridge.receiver from AAOS app frames per
    # ex30-companion-aaos/pi_bridge/protocol.md. Field names mirror the wire schema 1:1
    # so the receiver can call update(**fields) directly. _aaos suffix
    # marks fields with an OBD2 counterpart (kept distinct so divergence
    # is observable); bare names have no OBD2 source.
    aaos_stale: bool = True                # True until first frame; flipped
                                           # back to True after >15s silence.
    aaos_last_msg_ts: float = 0.0          # epoch seconds, last frame received
    speed_aaos: float = 0.0                # m/s (signed; negative = reverse)
    speed_display_aaos: float = 0.0        # m/s, dash-calibrated
    soc_aaos: float = 0.0                  # Wh (raw); convert to % using pack capacity
    battery_power_mw: float = 0.0          # raw VHAL mW: +charge/regen, -discharge
                                           # (AAOS v0.1.6 flips Volvo's inverted sign
                                           # so this matches the VHAL spec). The
                                           # receiver derives canonical `power_kw`,
                                           # throttle/regen pct, and energy integrals
                                           # from this.
    range_m_aaos: float = 0.0              # m
    gear_selected: str = ''                # P/R/N/D/UNKNOWN
    parking_brake: bool = False
    ignition_state: str = ''               # LOCK/OFF/ACC/ON/START/UNKNOWN
    # charge_port_open dropped — retail VHAL never flips it on the real EX30.
    charge_port_connected: bool = False
    ambient_temp_aaos: float = 0.0         # °C
    night_mode: bool = False
    pack_capacity_wh: float = 0.0          # Wh, INFO_EV_BATTERY_CAPACITY,
                                           # STATIC — populated once on connect,
                                           # used by receiver for soc% conversion.


class SharedVehicleData:
    """Thread-safe wrapper around VehicleData.

    Poller thread calls update() to set fields.
    UI thread calls snapshot() to get a frozen copy for rendering.
    """

    def __init__(self):
        self._data = VehicleData()
        self._lock = threading.Lock()

    def update(self, **kwargs) -> None:
        """Set one or more fields atomically."""
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._data, key, value)

    def snapshot(self) -> VehicleData:
        """Return a shallow copy for the UI to read without holding the lock."""
        with self._lock:
            return copy.copy(self._data)

    def get(self, field_name: str):
        """Read a single field (for poller-internal use like energy integration)."""
        with self._lock:
            return getattr(self._data, field_name)
