"""AAOS bridge — receives the 13 v1 wire fields from the EX30 companion app
running on the head unit and feeds them into SharedVehicleData.

Protocol: ex30-companion-aaos/pi_bridge/protocol.md.
"""

from aaos_bridge.receiver import AaosBridgeReceiver, WIRE_FIELDS

__all__ = ["AaosBridgeReceiver", "WIRE_FIELDS"]
