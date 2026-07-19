"""Shared test fixtures."""

import sys
from pathlib import Path

import pytest

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings, BluetoothConfig, PollingIntervals
from obd2.pids import PIDRegistry


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def bt_config():
    return BluetoothConfig()


@pytest.fixture
def polling_intervals():
    return PollingIntervals()


@pytest.fixture
def pid_registry():
    return PIDRegistry()
