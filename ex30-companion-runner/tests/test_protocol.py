"""Tests for ELM327 protocol layer (uses mock serial connection)."""

from __future__ import annotations

from unittest.mock import MagicMock
from obd2.connection import OBD2Connection
from obd2.protocol import ELMProtocol
from obd2.pids import BECM, GATEWAY_11BIT, ECU_E


def _make_mock_connection(responses: list[str] | None = None) -> OBD2Connection:
    """Create a mock OBD2Connection that returns canned responses."""
    conn = MagicMock(spec=OBD2Connection)
    if responses:
        conn.send = MagicMock(side_effect=responses)
    else:
        conn.send = MagicMock(return_value="OK")
    return conn


class TestELMProtocol:
    def test_initialize_success(self):
        # 8 init commands → 8 responses
        responses = ["ELM327 v1.5", "OK", "OK", "OK", "OK", "OK", "OK", "OK"]
        conn = _make_mock_connection(responses)
        protocol = ELMProtocol(conn)

        assert protocol.initialize() is True
        assert protocol.initialized is True
        assert conn.send.call_count == 8

    def test_initialize_failure(self):
        conn = _make_mock_connection([])
        conn.send.side_effect = Exception("serial error")
        protocol = ELMProtocol(conn)

        assert protocol.initialize() is False
        assert protocol.initialized is False

    def test_query_pid_before_init(self):
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)

        assert protocol.query_pid(0x01, 0x0D) is None

    def test_query_pid_success(self):
        conn = _make_mock_connection(["OK"] * 8 + ["410D3C"])
        protocol = ELMProtocol(conn)
        protocol.initialize()

        result = protocol.query_pid(0x01, 0x0D)
        assert result == "410D3C"

    def test_query_pid_no_data(self):
        conn = _make_mock_connection(["OK"] * 8 + ["NO DATA"])
        protocol = ELMProtocol(conn)
        protocol.initialize()

        result = protocol.query_pid(0x01, 0x0D)
        assert result is None

    def test_query_raw(self):
        conn = _make_mock_connection(["OK"] * 8 + ["12.6V"])
        protocol = ELMProtocol(conn)
        protocol.initialize()

        result = protocol.query_raw("ATRV")
        assert result == "12.6V"


class TestECUSwitching:
    def test_switch_none_always_ok(self):
        """ELM direct commands need no switching."""
        conn = _make_mock_connection(["OK"] * 7)
        protocol = ELMProtocol(conn)
        protocol.initialize()

        assert protocol.switch_ecu(None) is True

    def test_switch_becm(self):
        """Switching to BECM sends ATSP7 + header/filter/FC commands."""
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        assert protocol.switch_ecu(BECM) is True
        assert protocol._current_ecu == BECM
        assert protocol._current_protocol == 7

        # Verify the AT commands sent
        calls = [c.args[0] for c in conn.send.call_args_list]
        assert "ATSP7" in calls
        assert "ATSHD01635" in calls
        assert "ATCRA1EC6AE80" in calls
        assert "ATFCSH1DD01635" in calls
        assert "ATFCSD300000" in calls
        assert "ATFCSM1" in calls

    def test_switch_same_ecu_is_noop(self):
        """No commands sent if already on the right ECU."""
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        protocol.switch_ecu(BECM)
        count_after_first = conn.send.call_count

        protocol.switch_ecu(BECM)
        assert conn.send.call_count == count_after_first

    def test_switch_29bit_to_11bit(self):
        """Switching from 29-bit to 11-bit disables FC and resets headers."""
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        protocol.switch_ecu(BECM)
        conn.send.reset_mock()

        protocol.switch_ecu(GATEWAY_11BIT)
        calls = [c.args[0] for c in conn.send.call_args_list]
        assert "ATFCSM0" in calls
        assert "ATSP6" in calls
        assert "ATAR" in calls
        assert protocol._current_ecu == GATEWAY_11BIT

    def test_switch_none_to_11bit_clears_fc(self):
        """Even from unknown state, switching to 11-bit clears FC and headers."""
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True
        protocol._current_ecu = None
        protocol._current_protocol = None

        protocol.switch_ecu(GATEWAY_11BIT)
        calls = [c.args[0] for c in conn.send.call_args_list]
        assert "ATFCSM0" in calls
        assert "ATAR" in calls
        assert "ATSH7E3" in calls

    def test_switch_between_29bit_ecus(self):
        """Switching between two 29-bit ECUs only changes headers, not protocol."""
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        protocol.switch_ecu(BECM)
        conn.send.reset_mock()

        protocol.switch_ecu(ECU_E)
        calls = [c.args[0] for c in conn.send.call_args_list]
        # Should NOT resend ATSP7 — already on protocol 7
        assert "ATSP7" not in calls
        assert "ATSHD01701" in calls
        assert "ATCRA1EE02E80" in calls

    def test_switch_before_init_fails(self):
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)

        assert protocol.switch_ecu(BECM) is False

    def test_failed_switch_recovers_to_gateway(self):
        """After a failed ECU switch, adapter should recover to 11-bit gateway state.

        Regression test for ATSH persistence bug: if BECM switch fails mid-sequence,
        the next gateway switch must not leave a stale 29-bit header.
        """
        responses = []
        call_count = [0]

        def side_effect(cmd):
            call_count[0] += 1
            # ATSP7 succeeds, but ATSH fails → mid-sequence failure
            if cmd == "ATSHD01635":
                return "ERROR"
            return "OK"

        conn = _make_mock_connection()
        conn.send.side_effect = side_effect
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        # Start on gateway
        protocol.switch_ecu(GATEWAY_11BIT)
        conn.send.reset_mock()

        # Try switching to BECM — should fail at ATSH
        assert protocol.switch_ecu(BECM) is False
        assert protocol._current_ecu is None
        assert protocol._current_protocol is None

        # Recovery should have sent ATSP6 + ATSH7E3 to reset to gateway
        calls = [c.args[0] for c in conn.send.call_args_list]
        # Find recovery commands (after the failed switch)
        assert "ATSP6" in calls
        assert "ATSH7E3" in calls

    def test_gateway_works_after_failed_becm_switch(self):
        """Full round-trip: gateway → BECM (fails) → gateway should work cleanly."""
        call_log = []

        def side_effect(cmd):
            call_log.append(cmd)
            if cmd == "ATCRA1EC6AE80":
                return "ERROR"
            return "OK"

        conn = _make_mock_connection()
        conn.send.side_effect = side_effect
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        # Start on gateway
        assert protocol.switch_ecu(GATEWAY_11BIT) is True

        # Fail switching to BECM
        assert protocol.switch_ecu(BECM) is False

        # Switch back to gateway — should succeed since recovery ran
        call_log.clear()
        assert protocol.switch_ecu(GATEWAY_11BIT) is True
        assert protocol._current_ecu == GATEWAY_11BIT
        assert "ATSH7E3" in call_log


class TestQueryRawLogged:
    def test_success(self):
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        conn.send.return_value = "6248012710"
        raw, ok = protocol.query_raw_logged("224801")
        assert ok is True
        assert raw == "6248012710"

    def test_no_data(self):
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        conn.send.return_value = "NO DATA"
        raw, ok = protocol.query_raw_logged("224801")
        assert ok is False
        assert raw == "NO DATA"

    def test_error(self):
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        conn.send.return_value = "ERROR"
        raw, ok = protocol.query_raw_logged("224801")
        assert ok is False
        assert raw == "ERROR"

    def test_empty_response(self):
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)
        protocol.initialized = True

        conn.send.return_value = ""
        raw, ok = protocol.query_raw_logged("224801")
        assert ok is False
        assert raw == ""

    def test_not_initialized(self):
        conn = _make_mock_connection()
        protocol = ELMProtocol(conn)

        raw, ok = protocol.query_raw_logged("224801")
        assert ok is False
        assert raw == ""
