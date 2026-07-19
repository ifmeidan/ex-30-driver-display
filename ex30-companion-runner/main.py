"""Entry point for the EX30 Driver Display application.

Usage:
  python main.py           — dashboard UI (Qt Quick)
"""

import os
import sys
import logging
import signal
import argparse

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError as e:
        raise ImportError("Neither PySide6 nor PyQt6 is available") from e

from config.settings import Settings
from config.display import DisplayConfig
from obd2.connection import OBD2Connection
from obd2.protocol import ELMProtocol
from obd2.pids import PIDRegistry
from obd2.poller import Poller
from obd2.supervisor import LinkHealth, ObdSupervisor
from shared.vehicle_data import SharedVehicleData
from ui import screen_power
from shared.drive_logger import DriveLogger
from aaos_bridge.receiver import AaosBridgeReceiver

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log", mode="a"),
    ],
)
logger = logging.getLogger("main")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-drive-log", action="store_true",
                        help="Disable the per-drive CSV logger (default on)")
    parser.add_argument("--no-standby", action="store_true",
                        help="Disable parking standby (poll forever)")
    args = parser.parse_args()

    settings = Settings()
    display_config = DisplayConfig()
    registry = PIDRegistry()

    qt_app = QApplication(sys.argv)

    # Shared data model — poller writes, UI reads. Seed pack_capacity_wh
    # from settings so the Trip Log math has a real number even when AAOS
    # is offline; the receiver overwrites with the static VHAL value once
    # the first frame arrives.
    shared_data = SharedVehicleData()
    shared_data.update(pack_capacity_wh=settings.pack_capacity_wh)

    from ui.modern.app import ModernDisplayApp
    from ui.modern.backend import ConnectionStatus
    conn_status = ConnectionStatus()
    window = ModernDisplayApp(display_config, shared_data, conn_status)

    connection = OBD2Connection(settings.bluetooth)
    protocol = ELMProtocol(connection)
    health = LinkHealth()
    poller = Poller(
        protocol, registry, settings.polling,
        shared_data=shared_data,
        health=health,
    )

    if args.no_standby:
        settings.standby.enabled = False

    supervisor = ObdSupervisor(
        connection, protocol, poller, shared_data, health, settings.standby,
        conn_status=conn_status,
        # standby=True → screen off. Runs on the supervisor thread; the QML
        # black overlay (driven by obdState) is the Qt-side counterpart.
        on_standby_change=lambda standby: screen_power.set_screen(not standby),
    )

    aaos_bridge = AaosBridgeReceiver(
        shared_data,
        pack_capacity_wh=settings.pack_capacity_wh,
    )
    aaos_bridge.start()
    logger.info("AAOS bridge receiver started on :7878")

    drive_logger = None
    if not args.no_drive_log:
        drive_logger = DriveLogger(shared_data)
        drive_logger.start()

    def shutdown(*_):
        logger.info("Shutting down...")
        aaos_bridge.stop()
        if drive_logger is not None:
            drive_logger.stop()
        supervisor.stop()
        qt_app.quit()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # The supervisor owns connect/reconnect/standby on its own thread, so
    # the boot animation starts immediately; the loading screen renders
    # live connection state via conn_status.
    supervisor.start()

    window.show()
    exit_code = qt_app.exec()

    shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
