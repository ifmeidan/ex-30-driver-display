"""Qt Quick host window for the modern EX30 display.

A QQuickView (plain QWindow, no QWidget layer) loading qml/Main.qml. The QML
scene renders a 1920x480 canvas rotated -90° inside the 480x1920 OS window so
the Waveshare panel's hardware 90° CW rotation yields correct landscape —
the same mapping as the legacy RotatedView, but done on the GPU for free.
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtQml import qmlRegisterSingletonType
from PySide6.QtQuick import QQuickView

from ui.modern.backend import VehicleModel

logger = logging.getLogger(__name__)

QML_DIR = Path(__file__).resolve().parent / "qml"
FONT_PATH = Path(__file__).resolve().parent / "fonts" / "InterVariable.ttf"

_theme_registered = False


def _register_theme_singleton() -> None:
    global _theme_registered
    if not _theme_registered:
        qmlRegisterSingletonType(
            QUrl.fromLocalFile(str(QML_DIR / "Theme.qml")), "EX30", 1, 0, "Theme")
        _theme_registered = True


def _register_font() -> str:
    """Load the vendored Inter variable font; fall back to system sans."""
    if FONT_PATH.exists():
        font_id = QFontDatabase.addApplicationFont(str(FONT_PATH))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                logger.info("Loaded UI font: %s", families[0])
                return families[0]
    logger.warning("InterVariable.ttf missing/unreadable — using system font")
    return ""


class ModernDisplayApp(QQuickView):
    """Top-level Qt Quick window for the driver display.

    bench: optional QObject exposed to QML as `bench` for demo hotkeys
           (None in production — the car has no keyboard).
    """

    def __init__(self, display_config, shared_data, conn_status=None, *,
                 rotated: bool = True, skip_boot: bool = False,
                 bench=None, trip_state_path: str = "state/trip.json"):
        _register_theme_singleton()
        super().__init__()

        self._fullscreen = display_config.fullscreen
        self.model = VehicleModel(
            shared_data, conn_status, trip_state_path=trip_state_path,
            parent=self)

        family = _register_font()
        ctx = self.rootContext()
        ctx.setContextProperty("vehicle", self.model)
        ctx.setContextProperty("uiFontFamily", family)
        ctx.setContextProperty("uiRotated", rotated)
        ctx.setContextProperty("uiSkipBoot", skip_boot)
        ctx.setContextProperty("bench", bench)

        self.setColor(QColor("#0B0B0D"))  # window clear color — no white flash
        self.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self.setSource(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))

        if self.status() == QQuickView.Status.Error:
            for err in self.errors():
                logger.error("QML: %s", err.toString())
            raise RuntimeError("Modern UI failed to load — see QML errors above")

        if self._fullscreen:
            self.setCursor(Qt.CursorShape.BlankCursor)

        logger.info("Modern display initialized (rotated=%s, fullscreen=%s)",
                    rotated, self._fullscreen)

    def show(self):  # main.py calls window.show() on both UI paths
        if self._fullscreen:
            self.showFullScreen()
        else:
            super().show()
