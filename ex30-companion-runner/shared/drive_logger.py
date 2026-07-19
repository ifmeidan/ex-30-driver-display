"""Per-drive CSV logger.

Samples SharedVehicleData at a fixed rate and appends one row per
sample to a timestamped file under ``logs/drives/``. The goal is to
have raw, replayable evidence of what the dashboard saw during a real
drive so values (speed, instant power, regen, etc.) can be inspected
offline.

Disable by passing ``--no-drive-log`` to ``main.py``.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

from shared.vehicle_data import SharedVehicleData

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = os.path.join("logs", "drives")
DEFAULT_INTERVAL_S = 0.1  # 10 Hz — same cadence as the AAOS coalescer
                          # could push, but bounded so the file stays
                          # tractable for offline inspection.

# Fields written to the CSV. Kept narrow on purpose — every additional
# field bloats the file across an hour-long drive. Add more here if a
# specific test demands it.
FIELDS: tuple[str, ...] = (
    "timestamp",
    "speed",
    "gear",
    "soc",
    "power_kw",
    "throttle_pct",
    "regen_pct",
    "brake_pct",
    "consumed_kwh",
    "regen_kwh",
    "battery_temp",
    "ambient_temp",
    "odometer",
    "range_m_aaos",
    "is_charging",
    "aaos_stale",
)


class DriveLogger:
    """Background thread that snapshots SharedVehicleData on an interval
    and writes one CSV row per snapshot.

    Idempotent ``start()``/``stop()``; both are safe to call multiple
    times. The CSV file is flushed after every row so a hard power-cut
    on the Pi leaves a usable file."""

    def __init__(
        self,
        shared: SharedVehicleData,
        log_dir: str = DEFAULT_LOG_DIR,
        interval_s: float = DEFAULT_INTERVAL_S,
    ) -> None:
        self._shared = shared
        self._log_dir = log_dir
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._path: Optional[str] = None

    @property
    def path(self) -> Optional[str]:
        return self._path

    def start(self) -> None:
        if self._thread is not None:
            return
        os.makedirs(self._log_dir, exist_ok=True)
        fname = f"drive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self._path = os.path.join(self._log_dir, fname)
        self._thread = threading.Thread(
            target=self._run, name="drive-logger", daemon=True
        )
        self._thread.start()
        logger.info("drive log opened: %s", self._path)

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
        self._thread = None
        logger.info("drive log closed: %s", self._path)

    def _run(self) -> None:
        # Open with line buffering so partial writes survive a sudden
        # 12V drop; csv.writer handles quoting for the (rare) string
        # fields.
        try:
            f = open(self._path, "w", newline="", buffering=1, encoding="utf-8")
        except OSError as exc:
            logger.error("could not open %s: %s", self._path, exc)
            return
        try:
            w = csv.writer(f)
            w.writerow(FIELDS)
            while not self._stop.wait(self._interval_s):
                snap = self._shared.snapshot()
                w.writerow(_format_row(snap))
        finally:
            try:
                f.close()
            except OSError:
                pass


def _format_row(snap) -> list:
    ts = datetime.now().isoformat(timespec="milliseconds")
    return [
        ts,
        snap.speed,
        snap.gear,
        f"{snap.soc:.2f}",
        f"{snap.power_kw:.3f}",
        f"{snap.throttle_pct:.3f}",
        f"{snap.regen_pct:.3f}",
        f"{snap.brake_pct:.3f}",
        f"{snap.consumed_kwh:.4f}",
        f"{snap.regen_kwh:.4f}",
        f"{snap.battery_temp:.2f}",
        f"{snap.ambient_temp:.2f}",
        snap.odometer,
        f"{snap.range_m_aaos:.1f}",
        int(snap.is_charging),
        int(snap.aaos_stale),
    ]
