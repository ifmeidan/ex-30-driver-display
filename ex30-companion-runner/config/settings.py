"""Application-wide settings and polling intervals."""

from dataclasses import dataclass, field


@dataclass
class BluetoothConfig:
    """Bluetooth connection settings for the vLinker MC+."""
    mac_address: str = ""
    port: str = "/dev/rfcomm0"
    baud_rate: int = 115200
    timeout: float = 1.0
    rfcomm_channel: int = 1


@dataclass
class PollingIntervals:
    """OBD2 polling cadence.

    `fast` is the poller's base cycle interval (a floor, not a fixed rate —
    the serial round trips dominate, so a cycle actually takes as long as
    its queries do). MEDIUM/SLOW lanes are derived from it by cycle
    counters in obd2.poller (every 4th / every 20th cycle), not configured
    separately. Current lanes: brake pressure (FAST), HV current+voltage
    (MEDIUM), battery temp + odometer (SLOW)."""
    fast: float = 0.1


@dataclass
class StandbyConfig:
    """Parking standby — stop OBD polling and drop the Bluetooth link when
    the car looks left, so our diagnostic traffic can't hold the vehicle
    (and therefore the 12V/USB rail powering the Pi) awake overnight.

    Standby engages after `idle_after_s` with no activity signal, where
    activity is: AAOS reporting drive/charge state (ignition ON/ACC/START,
    gear not P, speed > 0, charging), or freshly-decoded OBD data showing
    |power| ≥ active_power_kw or brake pressure on the pedal. Wake is
    AAOS-driven (the infotainment reconnects/reports activity) or a Pi
    power cycle."""
    enabled: bool = True
    # Continuous quiet time before entering standby.
    idle_after_s: float = 150.0
    # AAOS silence after which its last-seen field values are ignored.
    aaos_gone_s: float = 180.0
    # Grace period after boot before standby is allowed at all.
    min_uptime_s: float = 120.0
    # No bytes back from the ELM327 for this long while polling = link dead
    # (dongle unplugged / BT dropped) → reconnect with backoff.
    link_dead_s: float = 12.0
    # |power_kw| at or above this counts as the car being used.
    active_power_kw: float = 1.0
    # brake_pct at or above this counts as a foot on the pedal.
    active_brake_pct: float = 0.05


@dataclass
class Settings:
    """Top-level application settings."""
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    polling: PollingIntervals = field(default_factory=PollingIntervals)
    standby: StandbyConfig = field(default_factory=StandbyConfig)
    log_dir: str = "logs"
    debug: bool = False
    # EX30 Long Range usable pack capacity. Used by the AAOS bridge to
    # convert raw Wh from VHAL into a display % SoC. Override for the
    # Standard Range (~51 kWh) or any future capacity.
    pack_capacity_wh: float = 64000.0
