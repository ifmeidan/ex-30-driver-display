"""Best-effort display power control for parking standby.

Called from the OBD supervisor thread (never from Qt) when standby is
entered/left. Software blanking cuts the backlight / video signal, not the
panel's 5V supply — that only dies when the sleeping car cuts USB power.

Two mechanisms, tried in order; both failing is fine because Main.qml also
draws a pure-black overlay while obdState == "standby":

1. sysfs backlight (`/sys/class/backlight/*/bl_power`) — DSI panels and
   most HATs. Needs write permission (udev rule or root).
2. `wlopm` (wlr-output-power-management client) — HDMI panels under a
   wlroots compositor such as cage. Only attempted if the binary exists.
"""

from __future__ import annotations

import glob
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

# Linux backlight convention: 0 = FB_BLANK_UNBLANK (on), 4 = FB_BLANK_POWERDOWN.
_BL_ON = "0"
_BL_OFF = "4"

_warned = False


def set_screen(on: bool) -> None:
    """Turn the display backlight/output on or off. Never raises."""
    global _warned
    ok = _sysfs_backlight(on)
    ok = _wlopm(on) or ok
    if not ok and not _warned:
        logger.info(
            "no controllable backlight found (sysfs empty, wlopm missing) — "
            "relying on the QML black overlay for standby"
        )
        _warned = True


def _sysfs_backlight(on: bool) -> bool:
    hit = False
    for path in glob.glob("/sys/class/backlight/*/bl_power"):
        try:
            with open(path, "w", encoding="ascii") as f:
                f.write(_BL_ON if on else _BL_OFF)
            hit = True
        except OSError as exc:
            logger.warning("backlight write failed (%s): %s", path, exc)
    return hit


def _wlopm(on: bool) -> bool:
    if shutil.which("wlopm") is None:
        return False
    try:
        subprocess.run(
            ["wlopm", "--on" if on else "--off", "*"],
            check=True, capture_output=True, timeout=5.0,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("wlopm failed: %s", exc)
        return False
