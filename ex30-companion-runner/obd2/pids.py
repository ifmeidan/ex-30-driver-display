"""PID definitions for standard OBD2 and Geely SEA (Volvo EX30) proprietary PIDs.

## Active poller lanes

The display only needs what AAOS doesn't provide. Everything else is
registered with PollGroup.NONE — kept for docs/scripts, never polled:

  BECM  (ATSHD01635): hv_current + hv_voltage (MEDIUM, power backup when
        AAOS is stale), hv_batt_temp_avg 491B (SLOW), odometer DD01 (SLOW)
  ECU-E (ATSHD01701): brake_pressure_multi (FAST) — all 4 channels FD00-FD03
        in ONE multi-DID request, averaged for the gauge; sequential FD00-FD03
        PIDs are the automatic fallback if multi-DID is unsupported

Both lanes are 29-bit (ATSP7) — cheap header-only switches, no protocol
flips. Most cycles are ECU-E-only (BECM visited every 4th/20th cycle), so
the brake gauge polls back-to-back with no context-switch latency.

## Protocol Split

Two separate CAN protocols:

  ATSP6 — ISO 15765-4, 11-bit CAN, 500kbps
    Gateway: 7E3 (request) -> 7EB (response)

  ATSP7 — ISO 15765-4, 29-bit CAN, 500kbps
    BECM:    ATSHD01635 / ATCRA1EC6AE80 — HV voltage/current (4801/4802),
             SoH (496D), avg/max HV temp (491B/4945), odometer (DD01)
    VCFRONT: ATSHD01601 / ATCRA1EC02E80 — dash SoC (D901)
    ECU-E:   ATSHD01701 / ATCRA1EE02E80 — brake FD00-FD03, speed (F40D),
             wheel speeds 2B06-2B09
    ECU-D / ECU-F: addresses confirmed (see docs/pid_map.md); no PIDs
             registered here — contexts kept for the research scripts

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class PollGroup(Enum):
    """Which polling rate a PID uses."""
    FAST = "fast"       # every cycle — brake pressure (the latency-critical lane)
    MEDIUM = "medium"   # every 4th cycle — HV current/voltage (power backup)
    SLOW = "slow"       # every 20th cycle — battery temp, odometer
    NONE = "none"       # registered for reference/scripts only — never polled.
                        # The display doesn't use these (AAOS covers speed/SoC/
                        # ambient); keeping them out of the lanes frees dongle
                        # bandwidth for the brake channels.


class ResearchStatus(Enum):
    CONFIRMED = "confirmed"     # Matched against known dashboard value
    CANDIDATE = "candidate"     # Responds and changes, decoding unverified
    UNKNOWN = "unknown"         # Responds but meaning unclear
    UNAVAILABLE = "unavailable" # Not exposed via OBD2 gateway


# ---------------------------------------------------------------------------
# ECU Context — defines the AT commands needed to talk to a specific ECU
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ECUContext:
    """AT command context for targeting a specific ECU."""
    name: str
    protocol: int       # 6 = 11-bit 500k, 7 = 29-bit 500k
    header: str = ""    # ATSH value (29-bit only)
    priority: str = ""  # ATCP value (29-bit only)
    rx_filter: str = "" # ATCRA value (29-bit only)
    fc_header: str = "" # ATFCSH value (29-bit only)
    fc_data: str = "300000"
    fc_mode: int = 1


# ECU context singletons
GATEWAY_11BIT = ECUContext(
    name="11-bit Gateway",
    protocol=6,
)

BECM = ECUContext(
    name="BECM",
    protocol=7,
    header="D01635",
    priority="1D",
    rx_filter="1EC6AE80",
    fc_header="1DD01635",
)

VCFRONT = ECUContext(
    name="VCFRONT",
    protocol=7,
    header="D01601",
    priority="1D",
    rx_filter="1EC02E80",
    fc_header="1DD01601",
)

ECU_D = ECUContext(
    name="ECU-D",
    protocol=7,
    header="D01650",
    priority="1D",
    rx_filter="1ECA0E80",
    fc_header="1DD01650",
)

ECU_E = ECUContext(
    name="ECU-E (Motor)",
    protocol=7,
    header="D01701",
    priority="1D",
    rx_filter="1EE02E80",
    fc_header="1DD01701",
)

ECU_F = ECUContext(
    name="ECU-F",
    protocol=7,
    header="D01637",
    priority="1D",
    rx_filter="1EC6EE80",
    fc_header="1DD01637",
)

# No ECU context needed — ELM327 direct commands (ATRV)
ELM_DIRECT = None


@dataclass
class PIDDefinition:
    """Describes how to request and decode a single OBD2 parameter."""
    name: str
    command: str            # Raw hex command to send (e.g. "22DD01")
    unit: str
    poll_group: PollGroup
    decoder: Callable[[str], float | None]
    ecu: ECUContext | None = None  # which ECU context to target
    description: str = ""
    proprietary: bool = False
    status: ResearchStatus = ResearchStatus.UNKNOWN
    notes: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_data(raw: str, did: str) -> str | None:
    """Strip spaces and multi-frame separators, extract data after 62XXXX header.

    ELM327 multi-frame ISO-TP responses look like:
      0: 62 DD 01 00 09 40
      1: AA BB CC DD ...
    The '0:', '1:', etc. are frame sequence indicators that must be stripped
    before interpreting the hex payload, otherwise byte indexing is wrong.
    """
    import re
    # Strip multi-frame sequence indicators (e.g. "0:", "1:", "2:" at line starts)
    clean = re.sub(r'[0-9A-Fa-f]:', '', raw)
    clean = clean.replace(" ", "").replace("\n", "").replace("\r", "").upper()
    marker = f"62{did.upper()}"
    idx = clean.find(marker)
    if idx == -1:
        return None
    return clean[idx + len(marker):]


# ---------------------------------------------------------------------------
# Decoders
# ---------------------------------------------------------------------------

def _decode_odometer(raw: str) -> float | None:
    """DID DD01 — Odometer in km. 3-byte big-endian integer."""
    data = _extract_data(raw, "DD01")
    if not data or len(data) < 6:
        return None
    try:
        return float(int(data[:6], 16))
    except ValueError:
        return None


def _decode_voltage(raw: str) -> float | None:
    """ELM327 direct 12V voltage via ATRV command.
    Response is typically '12.4V' but may include echo/prompt chars.
    """
    import re
    match = re.search(r'(\d+\.\d+)\s*[Vv]?', raw)
    if match:
        try:
            v = float(match.group(1))
            if 6.0 <= v <= 16.0:
                return v
        except ValueError:
            pass
    return None


def _decode_brake_multi(raw: str) -> float | None:
    """Multi-DID ReadDataByIdentifier of FD00-FD03 in ONE request.

    Expected payload (headers off, spaces off, multi-frame separators
    stripped): 62 FD00 xxxx FD01 xxxx FD02 xxxx FD03 xxxx.
    Returns the average of the four channels in bar (u16/100 each) —
    same-instant samples, one serial round trip instead of four.
    Returns None if the ECU didn't echo all four DIDs (e.g. multi-DID
    unsupported), which triggers the poller's sequential fallback.
    """
    import re
    clean = re.sub(r'[0-9A-Fa-f]:', '', raw)
    clean = clean.replace(" ", "").replace("\n", "").replace("\r", "").upper()
    m = re.search(
        r"62FD00([0-9A-F]{4}).*?FD01([0-9A-F]{4})"
        r".*?FD02([0-9A-F]{4}).*?FD03([0-9A-F]{4})",
        clean,
    )
    if not m:
        return None
    vals = [int(g, 16) for g in m.groups()]
    # Plausibility: max pedal effort is ~6300 raw (63 bar); reject garbage
    if any(v > 0x4000 for v in vals):
        return None
    return (sum(vals) / 4) / 100.0


def _decode_hv_voltage(raw: str) -> float | None:
    """DID 4801 (BECM, 29-bit) — HV pack voltage in V.
    Confirmed via BT snoop: 0xA73A (42810) / 100 = 428.10V.
    """
    data = _extract_data(raw, "4801")
    if not data or len(data) < 4:
        return None
    try:
        return int(data[:4], 16) / 100.0
    except ValueError:
        return None


def _decode_hv_soh(raw: str) -> float | None:
    """DID 496D (BECM, 29-bit) — HV Battery State of Health in %.
    Confirmed via BT snoop: 0x00002710 (10000) * 0.01 = 100.00%.
    """
    data = _extract_data(raw, "496D")
    if not data or len(data) < 8:
        return None
    try:
        return int(data[:8], 16) * 0.01
    except ValueError:
        return None


def _decode_soc_display(raw: str) -> float | None:
    """DID D901 (BECM or VCFRONT, 29-bit) — SoC as displayed on dashboard, integer %.
    Confirmed via BT snoop: 0x64 (100) = 100%.
    """
    data = _extract_data(raw, "D901")
    if not data or len(data) < 2:
        return None
    try:
        return float(int(data[:2], 16))
    except ValueError:
        return None


def _decode_speed_f40d(raw: str) -> float | None:
    """DID F40D (ECU-E, 29-bit) — Vehicle speed via UDS.
    Standard OBD2 PID 0D mapped to UDS DID F40D.
    Confirmed responding on ECU-E (D01701), value 00 at rest.
    Single byte = km/h.
    """
    data = _extract_data(raw, "F40D")
    if not data or len(data) < 2:
        return None
    try:
        return float(int(data[:2], 16))
    except ValueError:
        return None


def _decode_hv_current(raw: str) -> float | None:
    """DID 4802 (BECM, 29-bit) — HV Battery current in Amps.
    Formula: (raw − 16384) × 0.1 = A (signed via offset).
    Confirmed: +213A full throttle, −61A regen.
    Positive = discharge, negative = charge/regen.
    """
    data = _extract_data(raw, "4802")
    if not data or len(data) < 4:
        return None
    try:
        return (int(data[:4], 16) - 16384) * 0.1
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Generic raw decoders — for unknown DIDs during drive-test calibration
# ---------------------------------------------------------------------------

def _make_raw_decoder(
    did: str,
    num_bytes: int = 2,
    signed: bool = False,
    scale: float = 1.0,
    offset: float = 0.0,
) -> Callable[[str], float | None]:
    """Factory: create a decoder that extracts N bytes after 62{DID} header.

    Returns: (raw_int * scale) + offset.
    For signed values, interprets as two's complement.
    """
    hex_len = num_bytes * 2
    max_unsigned = 1 << (num_bytes * 8)

    def decoder(raw: str) -> float | None:
        data = _extract_data(raw, did)
        if not data or len(data) < hex_len:
            return None
        try:
            val = int(data[:hex_len], 16)
            if signed and val >= max_unsigned // 2:
                val -= max_unsigned
            return float(val) * scale + offset
        except ValueError:
            return None

    decoder.__doc__ = f"DID {did} — {num_bytes}B {'signed' if signed else 'unsigned'}, ×{scale}, +{offset}"
    return decoder


def _make_packed_decoder(
    did: str,
    byte_offset: int,
    num_bytes: int = 1,
    signed: bool = False,
    scale: float = 1.0,
    offset: float = 0.0,
) -> Callable[[str], float | None]:
    """Factory: extract specific byte(s) from a packed multi-byte DID response.

    byte_offset is the 0-based position in the data (after 62XXXX header).
    """
    hex_start = byte_offset * 2
    hex_end = hex_start + num_bytes * 2
    max_unsigned = 1 << (num_bytes * 8)

    def decoder(raw: str) -> float | None:
        data = _extract_data(raw, did)
        if not data or len(data) < hex_end:
            return None
        try:
            val = int(data[hex_start:hex_end], 16)
            if signed and val >= max_unsigned // 2:
                val -= max_unsigned
            return float(val) * scale + offset
        except ValueError:
            return None

    decoder.__doc__ = (f"DID {did} byte[{byte_offset}:{byte_offset+num_bytes}] "
                       f"{'signed' if signed else 'unsigned'}, ×{scale}, +{offset}")
    return decoder


# ---------------------------------------------------------------------------
# PID Registry
# ---------------------------------------------------------------------------

class PIDRegistry:
    """Central registry of all known and candidate PIDs for the Volvo EX30."""

    def __init__(self):
        self._pids: dict[str, PIDDefinition] = {}
        self._register_confirmed()
        self._register_candidates()

    def _register_confirmed(self) -> None:
        """PIDs confirmed by matching against known dashboard values."""

        self._register(PIDDefinition(
            name="odometer",
            command="22DD01",
            unit="km",
            poll_group=PollGroup.SLOW,
            decoder=_decode_odometer,
            ecu=BECM,
            description="Total vehicle odometer (BECM, 29-bit)",
            proprietary=True,
            status=ResearchStatus.CONFIRMED,
            notes="0x000940 = 2368 km. Confirmed on both the 11-bit gateway "
                  "and BECM (snoop 0009D1 = 2513 km). Polled on BECM so the "
                  "active lanes stay 29-bit only — no ATSP6/7 protocol flips.",
        ))

        self._register(PIDDefinition(
            name="voltage_12v",
            command="ATRV",
            unit="V",
            poll_group=PollGroup.NONE,
            decoder=_decode_voltage,
            ecu=ELM_DIRECT,
            description="12V auxiliary battery voltage (read by ELM adapter directly)",
            proprietary=False,
            status=ResearchStatus.CONFIRMED,
            notes="AT command — no ECU context switch needed.",
        ))

        # --- BECM PIDs (29-bit, ATSP7) ---

        self._register(PIDDefinition(
            name="hv_voltage",
            command="224801",
            unit="V",
            poll_group=PollGroup.MEDIUM,
            decoder=_decode_hv_voltage,
            ecu=BECM,
            description="HV pack voltage (BECM, 29-bit)",
            proprietary=True,
            status=ResearchStatus.CONFIRMED,
            notes="0xA73A=42810 ÷ 100 = 428.10V.",
        ))

        self._register(PIDDefinition(
            name="hv_soh",
            command="22496D",
            unit="%",
            poll_group=PollGroup.NONE,
            decoder=_decode_hv_soh,
            ecu=BECM,
            description="HV Battery State of Health (BECM, 29-bit)",
            proprietary=True,
            status=ResearchStatus.CONFIRMED,
            notes="0x00002710=10000 × 0.01 = 100.00%.",
        ))

        self._register(PIDDefinition(
            name="soc_display",
            command="22D901",
            unit="%",
            poll_group=PollGroup.NONE,
            decoder=_decode_soc_display,
            ecu=VCFRONT,
            description="SoC as shown on dashboard — integer % (VCFRONT, 29-bit)",
            proprietary=True,
            status=ResearchStatus.CONFIRMED,
            notes="0x64=100 → 100%. NRCs on BECM (7F2231), works on VCFRONT (D01601).",
        ))

        self._register(PIDDefinition(
            name="hv_current",
            command="224802",
            unit="A",
            poll_group=PollGroup.MEDIUM,
            decoder=_decode_hv_current,
            ecu=BECM,
            description="HV battery current (BECM, 29-bit). (raw-16384)*0.1 = A",
            proprietary=True,
            status=ResearchStatus.CONFIRMED,
            notes="DID 4802. +213A full throttle, −61A regen. "
                  "Positive=discharge, negative=charge/regen. "
                  "MEDIUM since 2026-07-16: power is a backup for stale AAOS "
                  "only — keeping it off the fast lane removes two ECU "
                  "switches per cycle so the brake gauge stays low-latency.",
        ))

        self._register(PIDDefinition(
            name="hv_batt_temp_avg",
            command="22491B",
            unit="°C",
            poll_group=PollGroup.SLOW,
            decoder=_make_packed_decoder("491B", byte_offset=0, num_bytes=2,
                                         scale=0.01, offset=-50.0),
            ecu=BECM,
            description="HV battery average temperature (BECM, 29-bit)",
            proprietary=True,
            status=ResearchStatus.CONFIRMED,
            notes="u16/100 - 50 = °C. Snoop 2026-07-16: 0x1FD1=8145 → 31.45°C, "
                  "matched the reference scanner app avg HV temp 31.45–31.46 exactly. "
                  "April snoop 0x19C3 → 15.95°C (cold battery) consistent.",
        ))

        self._register(PIDDefinition(
            name="hv_batt_temp_max",
            command="224945",
            unit="°C",
            poll_group=PollGroup.NONE,
            decoder=_make_packed_decoder("4945", byte_offset=1, num_bytes=2,
                                         scale=0.01, offset=-50.0),
            ecu=BECM,
            description="HV battery max cell temperature (BECM, 29-bit)",
            proprietary=True,
            status=ResearchStatus.CONFIRMED,
            notes="3 data bytes: byte0 = hottest sensor index (0x11=17 warm, "
                  "0x0B=11 in April), bytes1-2 u16/100 - 50 = °C. "
                  "Snoop 2026-07-16: 0x2026=8230 → 32.30°C, matched the reference scanner app max temp.",
        ))

    def _register_candidates(self) -> None:
        """PIDs that respond and change but whose decoding is not yet verified."""

        self._register(PIDDefinition(
            name="speed",
            command="22F40D",
            unit="km/h",
            poll_group=PollGroup.NONE,
            decoder=_decode_speed_f40d,
            ecu=ECU_E,
            description="Vehicle speed via UDS DID F40D on ECU-E (Motor)",
            proprietary=True,
            status=ResearchStatus.CANDIDATE,
            notes="Standard OBD2 PID 0D mapped to UDS F40D. Responds 00 at rest on "
                  "ECU-E (D01701). Needs driving data to confirm km/h scaling.",
        ))

        # hv_current is registered as CONFIRMED in _register_confirmed()

        # --- ERAD Motor PIDs (ECU-E, 29-bit) ---
        # FD00-FD03 are the 4 brake pressure channels (live watch 2026-07-16).
        # Primary read is ONE multi-DID request (brake_pressure_multi) for
        # minimum gauge latency; the four sequential PIDs below are the
        # automatic fallback if ECU-E rejects multi-DID (poller swaps lanes
        # after repeated failures).

        self._register(PIDDefinition(
            name="brake_pressure_multi",
            command="22FD00FD01FD02FD03",
            unit="bar",
            poll_group=PollGroup.FAST,
            decoder=_decode_brake_multi,
            ecu=ECU_E,
            description="All 4 brake pressure channels in one multi-DID "
                        "request — decoder returns their average (bar)",
            proprietary=True,
            status=ResearchStatus.CONFIRMED,
            notes="Latency fix 2026-07-16: 1 round trip instead of 4, "
                  "same-instant samples. Confirmed live same day — ECU-E "
                  "answers the combined request with no NO DATA/7F. Poller "
                  "still auto-falls-back to sequential FD00-FD03 after 5 "
                  "consecutive rejections as a safety net.",
        ))

        for name, did, ordinal in [
            ("brake_pressure", "FD00", "1st"),
            ("brake_pressure_b", "FD01", "2nd"),
            ("brake_pressure_c", "FD02", "3rd"),
            ("brake_pressure_d", "FD03", "4th"),
        ]:
            self._register(PIDDefinition(
                name=name,
                command=f"22{did}",
                unit="bar",
                poll_group=PollGroup.NONE,
                decoder=_make_raw_decoder(did, num_bytes=2, scale=0.01),
                ecu=ECU_E,
                description=f"Brake hydraulic pressure, {ordinal} channel "
                            f"(ECU-E {did})",
                proprietary=True,
                status=ResearchStatus.CONFIRMED,
                notes="raw/100 = bar, full scale ~62 bar (~900 psi) at max pedal "
                      "effort (2026-07-15 ramp + 2026-07-16 snoop/watch). "
                      "FD00-FD03 confirmed as the 4 channels via live watch "
                      "2026-07-16; near-identical at steady state (median "
                      "spread 0.17 bar). Gauge shows their average. "
                      "NONE by default — the poller promotes these to FAST "
                      "only if brake_pressure_multi fails in-car.",
            ))

        # --- Wheel speeds (ECU-E, 29-bit) ---
        # 2B06-2B09 on D01701, 1-byte each, 0 at rest. Confirmed via abs_bt_snoop.

        for name, did, pos in [
            ("wheel_speed_fl", "2B06", "front left"),
            ("wheel_speed_fr", "2B07", "front right"),
            ("wheel_speed_rl", "2B08", "rear left"),
            ("wheel_speed_rr", "2B09", "rear right"),
        ]:
            self._register(PIDDefinition(
                name=name,
                command=f"22{did}",
                unit="km/h?",
                poll_group=PollGroup.NONE,
                decoder=_make_raw_decoder(did, num_bytes=1),
                ecu=ECU_E,
                description=f"Wheel speed — {pos} (ECU-E, 1 byte)",
                proprietary=True,
                status=ResearchStatus.CANDIDATE,
                notes=f"DID {did} on ECU-E (D01701). 1-byte response, 0x00 at rest. "
                      "Confirmed responding via abs_bt_snoop. Scale likely 1:1 km/h.",
            ))

    # ------------------------------------------------------------------

    def _register(self, pid: PIDDefinition) -> None:
        self._pids[pid.name] = pid

    def get(self, name: str) -> PIDDefinition | None:
        return self._pids.get(name)

    def get_by_group(self, group: PollGroup) -> list[PIDDefinition]:
        """Return queryable PIDs in a polling group (confirmed or candidate with a command)."""
        return [
            p for p in self._pids.values()
            if p.poll_group == group
            and p.command
            and p.status != ResearchStatus.UNAVAILABLE
        ]

    def get_by_ecu(self, ecu: ECUContext | None) -> list[PIDDefinition]:
        """Return all queryable PIDs targeting a specific ECU context."""
        return [
            p for p in self._pids.values()
            if p.ecu == ecu
            and p.command
            and p.status != ResearchStatus.UNAVAILABLE
        ]

    def ecu_contexts(self) -> list[ECUContext | None]:
        """Return distinct ECU contexts that have queryable PIDs."""
        seen: list[ECUContext | None] = []
        for p in self._pids.values():
            if p.command and p.status != ResearchStatus.UNAVAILABLE:
                if p.ecu not in seen:
                    seen.append(p.ecu)
        return seen

    def all_available(self) -> list[PIDDefinition]:
        """All PIDs that have commands and are not marked unavailable."""
        return [p for p in self._pids.values()
                if p.command and p.status != ResearchStatus.UNAVAILABLE]

    def confirmed(self) -> list[PIDDefinition]:
        return [p for p in self._pids.values() if p.status == ResearchStatus.CONFIRMED]

    def candidates(self) -> list[PIDDefinition]:
        return [p for p in self._pids.values() if p.status == ResearchStatus.CANDIDATE]

    def unavailable(self) -> list[PIDDefinition]:
        return [p for p in self._pids.values() if p.status == ResearchStatus.UNAVAILABLE]
