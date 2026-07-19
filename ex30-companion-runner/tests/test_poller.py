"""Tests for the ECU-batched poller.

The mock protocol answers the two active FAST lanes with decodable
payloads: brake_pressure_multi (ECU-E multi-DID read) and hv_current
(BECM 4802). Everything else gets NO DATA, like a car that's awake but
only serving those ECUs.
"""

import time
from unittest.mock import MagicMock

from config.settings import PollingIntervals
from obd2.protocol import ELMProtocol
from obd2.pids import PIDRegistry, PollGroup
from obd2.poller import Poller
from obd2.supervisor import LinkHealth
from shared.vehicle_data import SharedVehicleData

# 4 × 0x0100 = 2.56 bar per channel → decodes to 2.56 bar average.
BRAKE_MULTI_RESP = "62FD000100FD010100FD020100FD030100"
# 0x4000 = 16384 → (16384 - 16384) × 0.1 = 0.0 A.
HV_CURRENT_RESP = "624802400000"


def _respond(command: str):
    if "FD00" in command:
        return (BRAKE_MULTI_RESP, True)
    if "4802" in command:
        return (HV_CURRENT_RESP, True)
    return ("NO DATA", False)


def _make_mock_protocol() -> ELMProtocol:
    proto = MagicMock(spec=ELMProtocol)
    proto.initialized = True
    proto.query_raw_logged = MagicMock(side_effect=_respond)
    proto.switch_ecu = MagicMock(return_value=True)
    return proto


def _make_poller(proto, **kwargs) -> Poller:
    registry = kwargs.pop("registry", PIDRegistry())
    intervals = PollingIntervals(fast=0.05)
    return Poller(proto, registry, intervals,
                  shared_data=SharedVehicleData(), **kwargs)


class TestPoller:
    def test_start_and_stop(self):
        proto = _make_mock_protocol()
        callback = MagicMock()

        poller = _make_poller(proto, callback=callback)
        poller.start()
        time.sleep(0.3)
        poller.stop()

        # Decoded FAST-lane values must have reached the decoded-value callback.
        assert callback.call_count > 0

    def test_ecu_switch_called(self):
        """Poller should call switch_ecu for each ECU group that has due PIDs."""
        proto = _make_mock_protocol()

        poller = _make_poller(proto, callback=MagicMock())
        poller.start()
        time.sleep(0.3)
        poller.stop()

        assert proto.switch_ecu.call_count > 0

    def test_latest_values_stored(self):
        proto = _make_mock_protocol()

        poller = _make_poller(proto, callback=MagicMock())
        poller.start()
        time.sleep(0.3)
        poller.stop()

        assert len(poller.latest) > 0

    def test_ecu_groups_built(self):
        """ECU groups cover exactly the pollable (non-NONE) PIDs."""
        registry = PIDRegistry()
        proto = _make_mock_protocol()

        poller = _make_poller(proto, registry=registry, callback=MagicMock())

        # 2026-07-16 lanes: ECU-E (brake) + BECM (power backup / temps / odo).
        assert len(poller._ecu_groups) >= 2

        total_grouped = sum(len(pids) for pids in poller._ecu_groups.values())
        total_pollable = sum(
            1 for p in registry.all_available() if p.poll_group != PollGroup.NONE
        )
        assert total_grouped == total_pollable

    def test_single_thread(self):
        """Poller should use exactly one thread."""
        proto = _make_mock_protocol()

        poller = _make_poller(proto, callback=MagicMock())
        poller.start()
        assert poller._thread is not None
        assert poller._thread.is_alive()
        poller.stop()

    def test_switch_failure_skips_ecu(self):
        """If switch_ecu fails, those PIDs should be skipped, not crash."""
        proto = _make_mock_protocol()
        proto.switch_ecu = MagicMock(return_value=False)

        poller = _make_poller(proto, callback=MagicMock())
        poller.start()
        time.sleep(0.2)
        poller.stop()

        # No queries should have been made since all switches failed
        assert proto.query_raw_logged.call_count == 0

    def test_raw_callback_called(self):
        """When raw_callback is set, query_raw_logged is used and callback fires."""
        proto = _make_mock_protocol()
        raw_cb = MagicMock()

        poller = _make_poller(proto, callback=MagicMock(), raw_callback=raw_cb)
        poller.start()
        time.sleep(0.3)
        poller.stop()

        assert raw_cb.call_count > 0
        assert proto.query_raw_logged.call_count > 0

    # --- link health stamps -------------------------------------------------

    def test_health_stamped_on_responses(self):
        """Any adapter bytes stamp alive; only real decodes stamp decode."""
        proto = _make_mock_protocol()
        health = LinkHealth()

        poller = _make_poller(proto, callback=MagicMock(), health=health)
        poller.start()
        time.sleep(0.3)
        poller.stop()

        assert health.last_alive > 0.0
        assert health.last_decode > 0.0

    def test_health_no_alive_when_link_silent(self):
        """Empty responses (dead BT link) must not stamp alive."""
        proto = _make_mock_protocol()
        proto.query_raw_logged = MagicMock(return_value=("", False))
        health = LinkHealth()

        poller = _make_poller(proto, callback=MagicMock(), health=health)
        poller.start()
        time.sleep(0.3)
        poller.stop()

        assert proto.query_raw_logged.call_count > 0
        assert health.last_alive == 0.0
        assert health.last_decode == 0.0

    def test_health_alive_but_no_decode_on_no_data(self):
        """NO DATA = adapter alive, car ECUs asleep — alive but no decode."""
        proto = _make_mock_protocol()
        proto.query_raw_logged = MagicMock(return_value=("NO DATA", False))
        health = LinkHealth()

        poller = _make_poller(proto, callback=MagicMock(), health=health)
        poller.start()
        time.sleep(0.3)
        poller.stop()

        assert health.last_alive > 0.0
        assert health.last_decode == 0.0
