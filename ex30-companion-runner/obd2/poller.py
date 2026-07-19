"""ECU-batched single-thread OBD2 poller with rate-divided polling groups.

Architecture change from v1 (three independent threads):
  Old: 3 threads × 1 serial port = constant lock contention + protocol switching
       on every interleaved query (~600ms overhead per ECU switch).
  New: 1 thread that groups PIDs by ECU context, queries all PIDs for one ECU
       before switching to the next. Rate differentiation via cycle counters.

Data output:
  Primary: writes decoded values into SharedVehicleData (lock-free for UI).
  Optional: callback (pid_name, value, unit) — a generic decoded-value
            observer; unused by the dashboard, handy for logging/diagnostics.
  Optional: raw_callback for CSV/raw-response logging.

Derived signals:
  hv_power_kw = hv_current × hv_voltage / 1000
  consumed_kwh / regen_kwh = time-integrated from hv_power_kw
  throttle_pct / regen_pct = abs(power_kw) / 100, capped at 1.0

Cycle timing:
  base_interval (settings.polling.fast, 0.1s) is a floor, not a fixed
  rate — serial round trips dominate, so a cycle takes as long as its
  queries do (the FAST brake read alone is ~100-150ms).
  FAST PIDs:   every cycle
  MEDIUM PIDs: every 4th cycle
  SLOW PIDs:   every 20th cycle
  NONE PIDs:   never — reference-only, excluded from the lanes

Active lanes (2026-07-16 cleanup — everything else is PollGroup.NONE):
  ECU-E: brake_pressure_multi FD00-FD03 (FAST) → averaged into brake_pct
  BECM:  hv_current + hv_voltage (MEDIUM) → power backup when AAOS is
         stale; hv_batt_temp_avg 491B (SLOW); odometer DD01 (SLOW)
Both lanes are 29-bit — header-only ECU switches, no ATSP6/7 flips.
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Callable, Optional

from config.settings import PollingIntervals
from obd2.protocol import ELMProtocol
from obd2.pids import PIDRegistry, PIDDefinition, PollGroup, ECUContext
from shared.vehicle_data import SharedVehicleData

logger = logging.getLogger(__name__)

# Type alias for the decoded callback: (pid_name, decoded_value, unit)
DataCallback = Callable[[str, float, str], None]

# Type alias for the raw callback: (pid_name, command, raw_response, decoded_value_or_None, unit)
RawCallback = Callable[[str, str, str, Optional[float], str], None]

# Max power for throttle/regen bar scaling (kW)
POWER_BAR_MAX_KW = 100.0


class Poller:
    """Polls OBD2 PIDs on a single thread, grouped by ECU context.

    Minimizes expensive AT protocol switches by batching all queries
    for one ECU before moving to the next. Uses cycle counters to
    differentiate fast/medium/slow polling rates within the same loop.
    """

    FAST_EVERY = 1     # every cycle
    MEDIUM_EVERY = 4   # every 4th cycle
    SLOW_EVERY = 20    # every 20th cycle

    def __init__(
        self,
        protocol: ELMProtocol,
        registry: PIDRegistry,
        intervals: PollingIntervals,
        shared_data: SharedVehicleData,
        callback: Optional[DataCallback] = None,
        raw_callback: Optional[RawCallback] = None,
        health=None,
    ):
        self.protocol = protocol
        self.registry = registry
        self.intervals = intervals
        self.shared = shared_data
        self.callback = callback
        self.raw_callback = raw_callback
        # Optional obd2.supervisor.LinkHealth — stamped on every adapter
        # response / successful decode so the supervisor can tell a dead
        # BT link (silence) from a sleeping car (NO DATA).
        self.health = health

        self._running = False
        self._thread: threading.Thread | None = None
        self._cycle = 0

        # Base cycle interval — the fastest we loop
        self._base_interval = intervals.fast

        # Snapshot of latest values for each PID (internal tracking)
        self.latest: dict[str, float | None] = {}

        # Energy integration state
        self._last_power_time: float | None = None
        self._consumed_kwh: float = 0.0
        self._regen_kwh: float = 0.0

        # Multi-DID brake fallback state
        self._brake_multi_failures = 0
        self._lanes_dirty = False

        # Build ECU-grouped PID lists
        self._ecu_groups: dict[ECUContext | None, list[PIDDefinition]] = {}
        self._build_ecu_groups()

    def _build_ecu_groups(self) -> None:
        """Group pollable PIDs by their ECU context.

        PollGroup.NONE PIDs are registered for reference/scripts only and
        stay out of the lanes entirely (2026-07-16 cleanup: the display
        needs brake pressure, battery temp, odometer, and HV power backup —
        AAOS covers the rest).
        """
        self._ecu_groups.clear()
        for pid in self.registry.all_available():
            if pid.poll_group == PollGroup.NONE:
                continue
            ecu = pid.ecu
            if ecu not in self._ecu_groups:
                self._ecu_groups[ecu] = []
            self._ecu_groups[ecu].append(pid)

        for ecu, pids in self._ecu_groups.items():
            name = ecu.name if ecu else "ELM direct"
            logger.info("ECU group [%s]: %d PIDs — %s",
                        name, len(pids), ", ".join(p.name for p in pids))

    def _should_query(self, pid: PIDDefinition) -> bool:
        """Check if a PID is due this cycle based on its poll group."""
        if pid.poll_group == PollGroup.FAST:
            return self._cycle % self.FAST_EVERY == 0
        elif pid.poll_group == PollGroup.MEDIUM:
            return self._cycle % self.MEDIUM_EVERY == 0
        elif pid.poll_group == PollGroup.SLOW:
            return self._cycle % self.SLOW_EVERY == 0
        return False

    def start(self) -> None:
        """Start the polling thread."""
        if self._running:
            return

        self._running = True
        self._cycle = 0
        self._last_power_time = None
        self._consumed_kwh = 0.0
        self._regen_kwh = 0.0
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="poller",
        )
        self._thread.start()

        total = sum(len(pids) for pids in self._ecu_groups.values())
        logger.info("Poller started — %d PIDs across %d ECU contexts, base interval %.2fs",
                    total, len(self._ecu_groups), self._base_interval)

    def stop(self) -> None:
        """Signal the polling thread to stop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Poller stopped (trip: %.3f kWh consumed, %.3f kWh regen)",
                    self._consumed_kwh, self._regen_kwh)

    def _poll_loop(self) -> None:
        """Main polling loop — single thread, ECU-batched, rate-divided."""
        while self._running:
            cycle_start = time.monotonic()

            if self._lanes_dirty:
                self._build_ecu_groups()
                self._lanes_dirty = False

            for ecu, pids in self._ecu_groups.items():
                if not self._running:
                    break

                # Collect PIDs due this cycle for this ECU
                due = [p for p in pids if self._should_query(p)]
                if not due:
                    continue

                # Switch ECU context (no-op if already there)
                if not self.protocol.switch_ecu(ecu):
                    logger.warning("Failed to switch to %s, skipping %d PIDs",
                                   ecu.name if ecu else "ELM direct", len(due))
                    continue

                # Query all due PIDs for this ECU
                for pid_def in due:
                    if not self._running:
                        break
                    self._query_one(pid_def)

            self._cycle += 1

            # Sleep for remainder of base interval
            elapsed = time.monotonic() - cycle_start
            sleep_time = max(0, self._base_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _query_one(self, pid_def: PIDDefinition) -> None:
        """Query a single PID, decode, write to shared data, and optionally log."""
        try:
            # Single query path for both modes: query_raw_logged returns the
            # raw string even on NO DATA/ERROR, which the link-health logic
            # needs — any bytes back means the adapter is alive, even if the
            # car's ECUs are asleep. (Also drops query_raw's per-miss warning
            # that used to spam the log all night while parked.)
            raw, ok = self.protocol.query_raw_logged(pid_def.command)
            if self.health is not None and raw:
                self.health.mark_alive()
            value = pid_def.decoder(raw) if ok else None
            if self.health is not None and value is not None:
                self.health.mark_decode()
            if self.raw_callback:
                self.raw_callback(pid_def.name, pid_def.command, raw, value, pid_def.unit)
            got_response = ok

            # Multi-DID brake read: count consecutive *rejections* (ECU
            # answered, e.g. 7F 22 31, but decode failed) so we can fall
            # back to sequential FD00-FD03. NO DATA doesn't count — that's
            # a sleeping ECU (car not in READY), not a multi-DID rejection.
            if pid_def.name == self.BRAKE_MULTI_PID and got_response:
                if value is None:
                    self._brake_multi_failures += 1
                    if self._brake_multi_failures == self.BRAKE_MULTI_MAX_FAILURES:
                        self._fallback_to_sequential_brake()
                else:
                    self._brake_multi_failures = 0

            if value is not None:
                self.latest[pid_def.name] = value

                # Write to shared data model
                self._push_to_shared(pid_def.name, value)

                # Optional decoded-value observer (see class docstring).
                if self.callback:
                    self.callback(pid_def.name, value, pid_def.unit)

                # Derived: HV power (kW) = current × voltage
                if pid_def.name in ("hv_current", "hv_voltage"):
                    self._derive_power()

                # Derived: brake gauge (multi-DID average or channel average)
                if pid_def.name.startswith("brake_pressure"):
                    self._derive_brake()

        except Exception:
            logger.exception("Error querying PID %s", pid_def.name)

    # ── PID → SharedVehicleData field mapping ─────────────────────────────────

    # Entries beyond the active lanes are dormant but kept: soc_display and
    # speed are registered PIDs parked at PollGroup.NONE (re-enabling one is
    # a one-line poll_group change); outside_temp and hv_soc have no
    # registered PID at all today. The AAOS-priority logic is tested through
    # these dormant names either way.
    _PID_FIELD_MAP = {
        "outside_temp":     "ambient_temp",
        "odometer":         "odometer",
        "hv_soc":           "hv_soc",
        "soc_display":      "soc",
        "speed":            "speed",
        "hv_voltage":       "hv_voltage",
        "hv_current":       "hv_current",
        "hv_batt_temp_avg": "battery_temp",
    }

    # Fields AAOS provides authoritatively (see aaos_bridge.receiver.
    # AAOS_COVERED_OBD_FIELDS). When AAOS is fresh, we skip OBD writes for
    # these so the bridge value is not clobbered between bridge ticks.
    # Of these, only the power/energy group has an active OBD lane to take
    # over with when AAOS goes stale; speed/soc/ambient_temp PIDs are
    # parked at PollGroup.NONE, so those fields simply stop updating.
    _AAOS_COVERED = frozenset({
        "speed", "soc", "ambient_temp",
        "power_kw", "throttle_pct", "regen_pct", "consumed_kwh", "regen_kwh",
    })

    def _push_to_shared(self, pid_name: str, value: float) -> None:
        """Map a decoded PID value to the corresponding SharedVehicleData field."""
        field = self._PID_FIELD_MAP.get(pid_name)
        if not field:
            return
        if field in self._AAOS_COVERED and not self.shared.get("aaos_stale"):
            # AAOS is the live source for this field — drop the OBD write.
            return
        # odometer and speed are integers in the data model
        if field in ("odometer", "speed"):
            value = int(value)
        self.shared.update(**{field: value})

    # ── Derived signals ───────────────────────────────────────────────────────

    BRAKE_FULL_SCALE_BAR = 62.0

    BRAKE_MULTI_PID = "brake_pressure_multi"
    # Consecutive multi-DID misses before falling back to sequential reads.
    BRAKE_MULTI_MAX_FAILURES = 5

    _BRAKE_CHANNELS = ("brake_pressure", "brake_pressure_b",
                       "brake_pressure_c", "brake_pressure_d")

    def _derive_brake(self) -> None:
        """Feed the brake gauge from the multi-DID average, or the
        sequential FD00-FD03 channels after fallback."""
        avg_bar = self.latest.get(self.BRAKE_MULTI_PID)
        if avg_bar is None:
            readings = [self.latest[n] for n in self._BRAKE_CHANNELS
                        if n in self.latest]
            if not readings:
                return
            avg_bar = sum(readings) / len(readings)
        self.latest["brake_avg_bar"] = avg_bar
        brake_pct = min(max(avg_bar / self.BRAKE_FULL_SCALE_BAR, 0.0), 1.0)
        self.shared.update(brake_pct=brake_pct)
        if self.callback:
            self.callback("brake_avg_bar", avg_bar, "bar")

    def _fallback_to_sequential_brake(self) -> None:
        """Multi-DID read keeps failing — promote the four sequential
        FD00-FD03 PIDs to FAST and retire the multi PID. Lanes are rebuilt
        at the top of the next cycle (not here — we may be mid-iteration)."""
        multi = self.registry.get(self.BRAKE_MULTI_PID)
        if multi:
            multi.poll_group = PollGroup.NONE
        for name in self._BRAKE_CHANNELS:
            pid = self.registry.get(name)
            if pid:
                pid.poll_group = PollGroup.FAST
        self.latest.pop(self.BRAKE_MULTI_PID, None)
        self._lanes_dirty = True
        logger.warning(
            "brake_pressure_multi failed %d consecutive reads — ECU-E likely "
            "rejects multi-DID; falling back to sequential FD00-FD03",
            self.BRAKE_MULTI_MAX_FAILURES)

    def _derive_power(self) -> None:
        """Compute HV power, throttle/regen bars, and integrate energy."""
        current = self.latest.get("hv_current")
        voltage = self.latest.get("hv_voltage")
        if current is None or voltage is None:
            return

        power_kw = (current * voltage) / 1000.0
        self.latest["hv_power_kw"] = power_kw

        # Throttle/regen bar percentages (capped at POWER_BAR_MAX_KW)
        if power_kw > 0:
            throttle_pct = min(power_kw / POWER_BAR_MAX_KW, 1.0)
            regen_pct = 0.0
        else:
            throttle_pct = 0.0
            regen_pct = min(abs(power_kw) / POWER_BAR_MAX_KW, 1.0)

        # Energy integration (trapezoidal — simple dt × power)
        now = time.monotonic()
        if self._last_power_time is not None:
            dt_hours = (now - self._last_power_time) / 3600.0
            energy_kwh = abs(power_kw) * dt_hours
            if power_kw > 0:
                self._consumed_kwh += energy_kwh
            else:
                self._regen_kwh += energy_kwh
        self._last_power_time = now

        # Write all derived values to shared data — unless AAOS is fresh, in
        # which case the receiver is the authoritative source for these
        # (computed from EV_BATTERY_INSTANTANEOUS_CHARGE_RATE / battery_power_mw).
        # Keep updating the local _consumed_kwh / _regen_kwh integrators above
        # so OBD2 can take over instantly without a discontinuity when AAOS
        # goes stale.
        if self.shared.get("aaos_stale"):
            self.shared.update(
                power_kw=power_kw,
                throttle_pct=throttle_pct,
                regen_pct=regen_pct,
                consumed_kwh=self._consumed_kwh,
                regen_kwh=self._regen_kwh,
            )

        # Optional decoded-value observer (see class docstring).
        if self.callback:
            self.callback("hv_power_kw", power_kw, "kW")
