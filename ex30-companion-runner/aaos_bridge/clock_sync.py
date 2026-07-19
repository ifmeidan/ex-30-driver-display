"""Wall-clock sync from AAOS bridge frame timestamps.

The Pi may go days between car connections and has no RTC battery, so its
clock can be arbitrarily wrong on boot. Every bridge frame carries `ts`
(epoch ms, wall-clock UTC from `System.currentTimeMillis` in the AAOS app),
so the head unit's GPS-disciplined time is effectively a free time source.

This module owns the policy: when drift exceeds a threshold and we haven't
recently resynced, shell out to a privileged helper that calls `date -s`.
The helper is the only sudo surface; see `scripts/aaos-set-time` and
`scripts/aaos-set-time.sudoers`.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Callable, Optional

logger = logging.getLogger("aaos_bridge.clock_sync")

DEFAULT_HELPER = "/usr/local/sbin/aaos-set-time"
DEFAULT_DRIFT_THRESHOLD_S = 5.0
DEFAULT_MIN_RESYNC_INTERVAL_S = 300.0
HELPER_TIMEOUT_S = 2.0


class ClockSyncer:
    def __init__(
        self,
        helper_path: str = DEFAULT_HELPER,
        drift_threshold_s: float = DEFAULT_DRIFT_THRESHOLD_S,
        min_resync_interval_s: float = DEFAULT_MIN_RESYNC_INTERVAL_S,
        runner: Optional[Callable[..., "subprocess.CompletedProcess"]] = None,
        time_fn: Callable[[], float] = time.time,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._helper = helper_path
        self._threshold = drift_threshold_s
        self._min_interval = min_resync_interval_s
        self._run = runner if runner is not None else subprocess.run
        self._now = time_fn
        self._mono = monotonic_fn
        self._last_attempt_mono: Optional[float] = None
        # Auto-disable if the helper isn't installed; log once at startup.
        self._enabled = os.access(helper_path, os.X_OK)
        if not self._enabled:
            logger.info(
                "clock sync disabled: helper not installed at %s "
                "(run scripts/install_clock_sync.sh on the Pi to enable)",
                helper_path,
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def consider(self, frame_ts_ms: object) -> bool:
        """Maybe sync the system clock from a frame's `ts`. Returns True if a
        sync was attempted (regardless of outcome)."""
        if not self._enabled:
            return False
        try:
            frame_ts_s = float(frame_ts_ms) / 1000.0
        except (TypeError, ValueError):
            return False
        # `ts` of 0/1 shows up in tests; ignore obviously-bogus values rather
        # than blasting the clock back to 1970.
        if frame_ts_s < 1_700_000_000:  # ~2023-11-15
            return False

        drift = abs(frame_ts_s - self._now())
        if drift < self._threshold:
            return False

        last = self._last_attempt_mono
        if last is not None and (self._mono() - last) < self._min_interval:
            return False

        self._last_attempt_mono = self._mono()
        epoch_int = int(frame_ts_s)
        try:
            res = self._run(
                ["sudo", "-n", self._helper, str(epoch_int)],
                capture_output=True,
                text=True,
                timeout=HELPER_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("clock sync helper failed to launch: %s", exc)
            return True
        if res.returncode == 0:
            logger.info(
                "clock synced to %d (drift was %.1fs)", epoch_int, drift
            )
        else:
            logger.warning(
                "clock sync rc=%d stderr=%r (drift was %.1fs)",
                res.returncode, (res.stderr or "").strip(), drift,
            )
        return True
