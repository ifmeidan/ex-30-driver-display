# EX30 OBD2 PID Map — Confirmed ECU Addresses & DID Reference

**Vehicle**: Volvo EX30 (Geely SEA platform)
**Protocol**: ISO 15765-4 (CAN), 29-bit headers, 500 kbps (`ATSP7`), plus a small 11-bit gateway lane (`ATSP6`)
**Adapter**: vLinker MC+ (ELM327-compatible) over Bluetooth RFCOMM

Everything in this document was confirmed on a real car.

---

## How this map was built (the method)

The EX30 exposes almost nothing through standard OBD2 Mode 01 — the
useful data lives behind proprietary UDS `ReadDataByIdentifier` (`22 xxxx`)
requests on manufacturer ECU addresses. The map was recovered in four
passes:

1. **Bluetooth HCI snoop capture.** With an off-the-shelf OBD2 scanner app
   talking to the same adapter, Android's Bluetooth HCI snoop log was
   enabled on the phone, a full polling session was recorded, and the log
   was extracted via `adb bugreport`. Parsing the RFCOMM payloads out of
   the btsnoop file recovers the exact ELM327 init sequence, the ECU
   headers (`ATSH…`), the response filters (`ATCRA…`), every DID queried,
   and every raw response — a complete, replayable transcript of what a
   working commercial implementation asks the car.

2. **Init + addressing replay.** The captured init sequence (below) was
   replayed from the Pi and each captured DID re-queried to verify the
   responses reproduce outside the original app.

3. **DID sweeps.** With known-good ECU addressing, the surrounding DID
   ranges were swept (`scripts/research/*_scan.py`, `becm_did_sweep.py`)
   to find neighbours the capture never touched. A key shortcut: the
   response filter is **derivable from the TX header** (verified against
   all five known ECUs):
   `CRA = 0x1EC02E80 + ((hdr16 − 0x1601) << 13)` where `hdr16` is the low
   16 bits of the `D0xxxx` header (e.g. `D01635` → `0x1635` → `1EC6AE80`).
   That lets you probe unknown ECU addresses without guessing response IDs.

4. **Live calibration drives.** Every candidate was cross-checked against
   the car's own dashboard (or physically known values, e.g. tyre
   pressures) during parked and driving sessions
   (`scripts/research/candidate_calibration.py`, `*_watch.py`). Only
   signals that tracked reality earned a ✅ here.

---

## ELM327 init sequence (confirmed working)

```
ATZ
ATE0
ATH1
ATSP7
ATS0
ATM0
ATAT1
ATSHD01635        ← BECM header (default ECU)
ATCP1D
ATCRA1EC6AE80     ← BECM response filter
ATFCSH1DD01635    ← Flow-control send header
ATFCSD300000      ← Flow-control data: 30 00 00
ATFCSM1           ← Flow-control mode: custom
```

Connection test: `224801` (HV voltage) then `22491B` (avg pack temp) —
if both answer, the ECU link is live.

---

## ECU address map (confirmed)

| ECU | TX header (`ATSH`) | Full TX ID | RX filter (`ATCRA`) | Notes |
|-----|-------------------|-----------|--------------------|-------|
| **BECM** | `D01635` | `0x1DD01635` | `1EC6AE80` | Battery Energy Control Module |
| **VCFRONT** | `D01601` | `0x1DD01601` | `1EC02E80` | Gateway / front ECU |
| **ECU-E** | `D01701` | `0x1DD01701` | `1EE02E80` | Motor/power ECU — brake pressure, speed |
| **ECU-F** | `D01637` | `0x1DD01637` | `1EC6EE80` | E30x DIDs respond here (not on `D01631`) |
| **ECU-D** | `D01650` | `0x1DD01650` | `1ECA0E80` | Identity unknown — active `EE0x` DIDs |

Per-ECU flow control follows the same pattern as BECM:
`ATFCSH1D<hdr>` + `ATFCSD300000` + `ATFCSM1`.

---

## Confirmed DIDs

### BECM — `ATSHD01635` / `ATCRA1EC6AE80`

| DID | Signal | Decode | Confirmation |
|-----|--------|--------|--------------|
| `4801` | HV pack voltage | u16 ÷ 100 = V | 407–410 V at 85 % SoC; tracks load |
| `4802` | **HV pack current** | (u16 − 16384) × 0.1 = A | Matches DC-DC draw at rest; peak +213 A full throttle, −61 A regen |
| `491B` | HV pack avg temperature | u16 ÷ 100 − 50 = °C | Cross-checked in two seasons (15.95 °C cold, 31.45 °C warm) |
| `4945` | HV pack max temperature | byte0 = hottest sensor index; u16@1 ÷ 100 − 50 = °C | Consistent with `491B`, sensor index plausible |
| `496D` | HV battery SoH | u32 × 0.01 = % | Matches dash 100 % |
| `DD01` | Odometer | 3-byte BE = km | Matches dash exactly |

**Derived: HV power.** `power_W = (raw_4802 − 16384) × 0.1 × (raw_4801 / 100)`.
Positive = drive, negative = regen. Validated live: ~370 W at rest
(DC-DC), ~86.8 kW peak, ~−25 kW lift-off regen. This drives the
display's power/regen gauge when the AAOS value is stale.

### ECU-E — `ATSHD01701` / `ATCRA1EE02E80`

| DID | Signal | Decode | Confirmation |
|-----|--------|--------|--------------|
| `FD00`–`FD03` | Brake pressure, 4 channels | u16 ÷ 100 = bar (0–62 bar full scale) | Tracks pedal effort; channels near-identical at steady state; display gauge averages the four |
| `F40D` | Vehicle speed (OBD-standard PID as UDS DID) | u8 = km/h | Matches dash within 1 km/h on drive test |
| `2B06`–`2B09` | Wheel speeds FL/FR/RL/RR | u8 ≈ km/h | Track dash speed on drive test |

The four brake-pressure DIDs answer a **single multi-DID request**
(`22 FD00 FD01 FD02 FD03`), giving one round trip and same-instant
samples; the poller falls back to sequential reads if the ECU rejects
the multi-DID form. That cut brake-gauge latency from ~600–800 ms to
~100–150 ms.

### VCFRONT — `ATSHD01601` / `ATCRA1EC02E80`

| DID | Signal | Decode | Confirmation |
|-----|--------|--------|--------------|
| `D901` | SoC as displayed on dash | u8 = % | Matches dash. Note: answers on VCFRONT — on BECM the same DID NRCs (`7F 22 31`) |

### 11-bit gateway — `ATSP6` / `ATSH7E3`

| DID | Signal | Decode | Confirmation |
|-----|--------|--------|--------------|
| `DD01` | Odometer (also on BECM) | 3-byte BE = km | Matches dash |

**Gotcha:** the transmit header set by `ATSH` **persists across `ATSP`
protocol switches** — when dropping from 29-bit to 11-bit you must
re-issue `ATSH7E3`, or the gateway queries go out with a stale header.

### Adapter-local

| Query | Signal | Decode |
|-------|--------|--------|
| `ATRV` | 12 V battery voltage | float V (15.2 V is normal DC-DC output while awake) |

---

## What production actually polls

The AAOS companion app covers speed, SoC, range, ambient temperature,
gear, and charging state with better latency than the dongle, so the
production poller only queries what AAOS can't provide:

| ECU | DID | Signal | Rate | Decode |
|-----|-----|--------|------|--------|
| ECU-E `D01701` | `FD00 FD01 FD02 FD03` (one multi-DID request) | Brake pressure ×4 → gauge average | FAST | u16 ÷ 100 = bar |
| BECM `D01635` | `4802` | HV current (power backup when AAOS stale) | MEDIUM | (raw − 16384) × 0.1 A |
| BECM `D01635` | `4801` | HV voltage (power backup when AAOS stale) | MEDIUM | u16 ÷ 100 = V |
| BECM `D01635` | `491B` | HV battery avg temperature | SLOW | u16 ÷ 100 − 50 = °C |
| BECM `D01635` | `DD01` | Odometer | SLOW | 3-byte BE km |

Everything else stays registered in `obd2/pids.py` as `PollGroup.NONE` —
available to the research scripts, zero dongle traffic in daily use.
Most poll cycles are ECU-E-only (BECM joins every 4th/20th cycle), and
the ECU-switch AT sequence runs without inter-command sleeps.
