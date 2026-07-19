"""Tests for configuration dataclasses."""

from config.settings import Settings, BluetoothConfig, PollingIntervals
from config.display import DisplayConfig


class TestBluetoothConfig:
    def test_defaults(self):
        cfg = BluetoothConfig()
        assert cfg.port == "/dev/rfcomm0"
        assert cfg.baud_rate == 115200
        assert cfg.timeout == 1.0

    def test_custom_values(self):
        cfg = BluetoothConfig(mac_address="AA:BB:CC:DD:EE:FF", baud_rate=9600)
        assert cfg.mac_address == "AA:BB:CC:DD:EE:FF"
        assert cfg.baud_rate == 9600


class TestPollingIntervals:
    def test_defaults(self):
        p = PollingIntervals()
        assert p.fast == 0.1


class TestDisplayConfig:
    def test_defaults(self):
        d = DisplayConfig()
        assert d.width == 480
        assert d.height == 1920
        assert d.fps == 60
        assert d.fullscreen is True


class TestSettings:
    def test_nested_defaults(self):
        s = Settings()
        assert s.bluetooth.port == "/dev/rfcomm0"
        assert s.polling.fast == 0.1
        assert s.debug is False
