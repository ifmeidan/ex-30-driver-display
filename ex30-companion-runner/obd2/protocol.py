"""ELM327/vLinker AT command protocol with ECU context switching.

Supports switching between multiple ECU contexts (11-bit gateway, BECM,
ECU-E, ECU-F, etc.) by sending the appropriate AT header/filter/flow-control
commands before querying DIDs.

"""

from __future__ import annotations

import logging
import time
from typing import Optional

from obd2.connection import OBD2Connection
from obd2.pids import ECUContext

logger = logging.getLogger(__name__)

# Base ELM327 init — run once at startup
INIT_COMMANDS = [
    ("ATZ", "Reset adapter", 2.0),
    ("ATE0", "Echo off", 0.2),
    ("ATE0", "Echo off (verify)", 0.2),
    ("ATL0", "Linefeeds off", 0.1),
    ("ATS0", "Spaces off", 0.1),
    ("ATH0", "Headers off", 0.1),
    ("ATM0", "Memory off", 0.1),
    ("ATAT1", "Adaptive timing on", 0.1),
]


class ELMProtocol:
    """Handles ELM327 initialization, ECU context switching, and PID queries."""

    def __init__(self, connection: OBD2Connection):
        self.connection = connection
        self.initialized = False
        self._current_ecu: ECUContext | None = None
        self._current_protocol: int | None = None

    def initialize(self) -> bool:
        """Run the base AT init sequence. Returns True on success."""
        logger.info("Initializing ELM327 protocol")
        for cmd, description, delay in INIT_COMMANDS:
            try:
                response = self.connection.send(cmd)
                logger.info("  %s (%s): %s", cmd, description, response)
                time.sleep(delay)
            except Exception:
                logger.exception("Init failed at command: %s", cmd)
                return False

        self.initialized = True
        self._current_ecu = None
        self._current_protocol = None
        logger.info("ELM327 initialized successfully")
        return True

    def switch_ecu(self, ecu: ECUContext | None) -> bool:
        """Switch to a different ECU context.

        Pass None for ELM327 direct commands (ATRV etc.) — no switch needed.
        Skips switching if already on the requested ECU context.
        Returns True on success.
        """
        if ecu is None:
            return True

        if ecu == self._current_ecu:
            return True

        if not self.initialized:
            logger.error("Cannot switch ECU — protocol not initialized")
            return False

        logger.debug("Switching ECU context: %s → %s",
                     self._current_ecu.name if self._current_ecu else "none", ecu.name)

        try:
            commands = self._build_switch_commands(ecu)
            # No inter-command sleep: connection.send() blocks until the
            # ELM's '>' prompt, which is its ready signal (latency fix
            # 2026-07-16 — the old 50ms sleeps added ~300ms per switch).
            for cmd in commands:
                resp = self.connection.send(cmd)
                if "ERROR" in resp:
                    logger.error("ECU switch failed at %s: %s", cmd, resp)
                    self._recover_from_failed_switch()
                    return False

            self._current_ecu = ecu
            self._current_protocol = ecu.protocol
            logger.debug("Now on ECU: %s", ecu.name)
            return True

        except Exception:
            logger.exception("ECU switch failed for %s", ecu.name)
            self._recover_from_failed_switch()
            return False

    def _recover_from_failed_switch(self) -> None:
        """Reset adapter to a known state after a failed ECU switch.

        A mid-sequence failure can leave stale ATSH/ATCRA/ATSP values.
        Reset protocol to 11-bit gateway defaults so the next switch starts clean.
        """
        self._current_ecu = None
        self._current_protocol = None
        try:
            for cmd in ["ATFCSM0", "ATAR", "ATSP6", "ATSH7E3"]:
                self.connection.send(cmd)
            logger.info("Recovered to 11-bit gateway after failed ECU switch")
        except Exception:
            logger.exception("Recovery after failed ECU switch also failed")

    def _build_switch_commands(self, ecu: ECUContext) -> list[str]:
        """Build the AT command sequence to switch to the given ECU context."""
        cmds: list[str] = []

        need_protocol_switch = (self._current_protocol != ecu.protocol)

        if need_protocol_switch:
            if self._current_protocol == 7 and ecu.protocol == 6:
                # 29-bit → 11-bit: disable custom flow control first
                cmds.append("ATFCSM0")
            cmds.append(f"ATSP{ecu.protocol}")

        if ecu.protocol == 6:
            # 11-bit gateway: clear FC mode, reset RX filter, set 11-bit header.
            # ATSP6 doesn't reset ATSH — must explicitly set 7E3 for gateway.
            # ATAR clears CRA (RX filter) but not ATSH (TX header).
            cmds.append("ATFCSM0")
            cmds.append("ATAR")
            cmds.append("ATSH7E3")
            return cmds

        # 29-bit ECU: set header, priority, RX filter, flow control
        if ecu.header:
            cmds.append(f"ATSH{ecu.header}")
        if ecu.priority:
            cmds.append(f"ATCP{ecu.priority}")
        if ecu.rx_filter:
            cmds.append(f"ATCRA{ecu.rx_filter}")
        if ecu.fc_header:
            cmds.append(f"ATFCSH{ecu.fc_header}")
            cmds.append(f"ATFCSD{ecu.fc_data}")
            cmds.append(f"ATFCSM{ecu.fc_mode}")

        return cmds

    def query_pid(self, mode: int, pid: int) -> str | None:
        """Send a standard OBD2 PID query and return the raw hex response.

        Example: query_pid(0x01, 0x0D) sends "010D" for vehicle speed.
        Returns None on error or empty response.
        """
        if not self.initialized:
            logger.error("Protocol not initialized")
            return None

        cmd = f"{mode:02X}{pid:02X}"
        response = self.connection.send(cmd)

        if not response or "NO DATA" in response or "ERROR" in response:
            logger.warning("PID query %s returned: %s", cmd, response)
            return None

        return response

    def query_raw(self, command: str) -> str | None:
        """Send an arbitrary command (for proprietary PIDs / AT commands).

        Use this for extended PIDs that don't follow standard mode/PID format.
        """
        if not self.initialized:
            logger.error("Protocol not initialized")
            return None

        response = self.connection.send(command)

        if not response or "NO DATA" in response or "ERROR" in response:
            logger.warning("Raw query %s returned: %s", command, response)
            return None

        return response

    def scan_for_ecu(
        self,
        header: str,
        rx_filter: str,
        fc_header: str,
        test_did: str = "F190",
        priority: str = "1D",
    ) -> bool:
        """Try to reach a 29-bit ECU at the given address.

        Sends a single UDS ReadDataByIdentifier for test_did (default F190 =
        VIN/part number, widely supported). Returns True if the ECU responds
        with valid data (62XXXX), False otherwise.
        """
        if not self.initialized:
            return False

        probe_ecu = ECUContext(
            name=f"probe-{header}",
            protocol=7,
            header=header,
            priority=priority,
            rx_filter=rx_filter,
            fc_header=f"{priority}{header}",
        )

        if not self.switch_ecu(probe_ecu):
            return False

        response = self.connection.send(f"22{test_did}")
        # Reset to no ECU so next switch is forced
        self._current_ecu = None
        self._current_protocol = None

        if not response:
            return False
        clean = response.replace(" ", "").upper()
        return f"62{test_did.upper()}" in clean

    def query_raw_logged(self, command: str) -> tuple[str, bool]:
        """Send a command and return (raw_response, success).

        Unlike query_raw, this always returns the response string — even
        for NO DATA / ERROR — so the caller can log it. The bool indicates
        whether the response looks valid (True) or is an error (False).
        """
        if not self.initialized:
            return ("", False)

        response = self.connection.send(command)

        if not response or "NO DATA" in response or "ERROR" in response:
            return (response or "", False)

        return (response, True)
