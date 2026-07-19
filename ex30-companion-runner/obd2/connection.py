"""Bluetooth serial connection to the vLinker MC+ OBD2 adapter."""

import logging
import serial

from config.settings import BluetoothConfig

logger = logging.getLogger(__name__)


class OBD2Connection:
    """Manages the serial connection over /dev/rfcomm0."""

    def __init__(self, config: BluetoothConfig):
        self.config = config
        self._serial: serial.Serial | None = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self) -> None:
        """Open the serial port to the vLinker."""
        if self.is_connected:
            logger.warning("Already connected")
            return

        logger.info("Connecting to %s at %d baud", self.config.port, self.config.baud_rate)
        self._serial = serial.Serial(
            port=self.config.port,
            baudrate=self.config.baud_rate,
            timeout=self.config.timeout,
        )
        logger.info("Connected")

    def disconnect(self) -> None:
        """Close the serial port."""
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("Disconnected")
        self._serial = None

    def send(self, command: str) -> str:
        """Send a command string and return the raw response.

        Appends carriage return, reads until the ELM327 prompt ('>').
        """
        if not self.is_connected:
            raise ConnectionError("Not connected to OBD2 adapter")

        cmd = (command.strip() + "\r").encode("ascii")
        self._serial.reset_input_buffer()
        self._serial.write(cmd)

        response = b""
        while True:
            chunk = self._serial.read(1)
            if not chunk:
                break  # timeout
            response += chunk
            if chunk == b">":
                break

        decoded = response.decode("ascii", errors="ignore").strip().rstrip(">").strip()
        logger.debug("TX: %s | RX: %s", command.strip(), decoded)
        return decoded

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()
