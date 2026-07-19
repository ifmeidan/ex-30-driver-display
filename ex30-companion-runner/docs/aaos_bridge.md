# AAOS Bridge

The EX30 head unit runs the Android Automotive companion app
(`ex30-companion-aaos/` in this repository) that subscribes to vehicle
properties via the `CarPropertyManager` API and pushes them to the Pi
over TCP. This is the Pi-side receiver.

**Wire protocol**: `ex30-companion-aaos/pi_bridge/protocol.md`.

## Module

`aaos_bridge/receiver.py` — `AaosBridgeReceiver(shared, host, port,
log_dir, stale_after_s, pack_capacity_wh)`. Started by `main.py`; runs in a
daemon thread.

- TCP server on `0.0.0.0:7878`, single connection (drops the previous on
  a new connect — matches protocol).
- Line-delimited JSON, max 4 KB per line. Bad lines logged and dropped.
- Whitelist of allowed wire fields enforced before any `setattr` on
  `VehicleData`, so a future schema bump can never silently set arbitrary
  attributes.
- Every accepted frame is appended to
  `logs/aaos_bridge_YYYYMMDD.jsonl` for offline replay.
- After 15s without any frame (heartbeat, state, etc.), `aaos_stale`
  flips back to `True`.

## Field mapping — AAOS is the live source when connected

The 13 v1 wire fields land on `SharedVehicleData` two ways:

1. **Raw `_aaos`-suffixed fields** for divergence diagnostics:
   `speed_aaos`, `soc_aaos` (Wh), `ambient_temp_aaos`, etc. These mirror
   the wire schema 1:1 in their native units.

2. **Canonical OBD-shared fields** — the receiver also writes the same
   data, unit-converted, onto the fields the UI was already binding to:

   | Wire field                                       | Canonical field      | Conversion                          |
   |--------------------------------------------------|----------------------|-------------------------------------|
   | `speed_display_aaos` (m/s), display-only         | `speed` (km/h)       | `round(m/s × 3.6)`                  |
   | `soc_aaos` (Wh)                                  | `soc` (%)            | `Wh / pack_capacity_wh × 100`       |
   | `ambient_temp_aaos`                              | `ambient_temp`       | identity (both °C)                  |
   | `gear`                                           | `gear`               | identity                            |
   | `charge_port_connected` (bool)                   | `is_charging`        | identity (drives the charging UI)   |
   | `battery_power_mw` (signed mW)                   | `charge_power_kw`    | mW ÷ 1e6 (signed; + = charging/regen) |

   `pack_capacity_wh` is auto-calibrated from the wire field of the same
   name (sourced from `INFO_EV_BATTERY_CAPACITY` on the head unit), which
   AAOS sends once per connect in the seed snapshot. The
   `Settings.pack_capacity_wh` value (default 64 kWh, EX30 LR) is just the
   pre-calibration fallback used until the first frame from AAOS arrives,
   or while the bridge is stale and the OBD2 path is computing SoC %.

   Notes:
   - **Speed is display-only (no fallback)**: AOSP's `PERF_VEHICLE_SPEED`
     is the raw wheel-speed value; `PERF_VEHICLE_SPEED_DISPLAY` is the
     calibrated number the dashboard speedometer shows (slightly higher
     than truth, by Volvo's own bias). The displayed `speed` is fed
     **only** from `speed_display_aaos` so the screen always matches the
     car's own speedometer. The raw `speed_aaos` is recorded in the
     `_aaos` namespace for divergence diagnostics but **must never** drive
     the display — it arrives at a higher cadence than the calibrated copy,
     so any fallback makes the digits bounce between sources. A frame that
     omits the calibrated value just leaves the last shown speed in place.
     (This fallback was intentionally removed; do not re-add it.) If the
     bridge goes stale entirely, the UI blanks the numeral to `–` rather
     than leave a frozen value on screen.
   - **Charging trigger**: the screen swap (drive ↔ charging) is driven by
     `is_charging`, which AAOS sets from `charge_port_connected`. Plug in
     the cable → screen flips, regardless of whether the car is actively
     drawing power yet.

### UI bindings driven by AAOS

- **Speed numeral**: `d.speed` (canonical, fed from `speed_display_aaos`).
- **SoC ring + percent**: `d.soc` (canonical, fed from `soc_aaos`).
- **Range tile** to the right of the SoC %, vertically tracking the SoC
  text position so the pair moves together with battery level.
  Hidden when the bridge is stale or no range is reported.
- **Theme** (`_is_day`): when the bridge is fresh, `night_mode` from the
  head unit is the source of truth. When the bridge is stale, falls back
  to the local clock (`DAY_START_HOUR..NIGHT_START_HOUR`).
- **Drive ↔ charging screen swap**: `d.is_charging` (canonical, fed from
  `charge_port_connected`).
- **Charging power readout**: `d.charge_power_kw` (canonical, derived
  from `battery_power_mw`).

The UI never branches on AAOS vs OBD — it just reads the canonical
field. The receiver and the poller cooperate via `aaos_stale` to make
sure exactly one of them is the writer at any moment.

## OBD2 suppression while AAOS is fresh — and what "takeover" really covers

The poller (`obd2/poller._push_to_shared`) consults `aaos_stale` and skips
writes for fields AAOS provides — so the slower OBD path doesn't clobber a
freshly-arrived bridge value between ticks. Fields with no AAOS
counterpart (`battery_temp`, `hv_voltage`, `hv_current`, `odometer`,
`brake_pct`) keep flowing from OBD2 unchanged regardless of bridge state.

When AAOS goes stale (no frames for 15 s) the suppression lifts, but that
only matters for fields the poller is actually polling. Be precise about
what happens per field:

- **Power / regen gauge + session energy — real hot standby.** The poller
  reads HV current and voltage (BECM, MEDIUM lane) the whole time and
  keeps its own energy integrators running, so when the bridge goes stale
  it takes over within a poll cycle, without a discontinuity.
- **Speed — no fallback, by design.** The OBD speed DID is registered but
  parked at `PollGroup.NONE` and never polled. On staleness the UI blanks
  the speed numeral to `–` (see `SpeedCluster.qml`) instead of freezing
  the last value or substituting the raw wheel speed.
- **SoC — dormant fallback only.** The dash-SoC DID (`D901` on VCFRONT)
  is confirmed and registered, but also `PollGroup.NONE`; on staleness the
  SoC display simply holds its last value. Promoting the DID back into a
  lane is a one-line `poll_group` change if you want a live OBD2 SoC.
- **Range** is hidden while stale; **ambient temperature** and **gear**
  hold their last value (there is no active OBD lane for either).

## Standalone / debugging

```
python -m aaos_bridge.receiver --port 7878 --log-dir logs
```

Prints a snapshot summary every 5s. Useful when bench-testing the
Android sender without the full UI.

## Wall-clock sync

The Pi has no RTC battery and is offline outside the car, so its system
clock is wrong on every boot. Every bridge frame already carries `ts`
(epoch ms, wall-clock UTC from `System.currentTimeMillis` in the AAOS
app), so the head unit's GPS-disciplined time is a free time source —
the receiver pulls from it instead of relying on NTP.

`aaos_bridge/clock_sync.py` — `ClockSyncer.consider(frame_ts_ms)`:
- If `|frame_ts - now| > 5s` AND it's been ≥ 5 min since the last sync
  attempt, shells out to `sudo -n /usr/local/sbin/aaos-set-time <epoch>`.
- Auto-disabled (no-op) if the helper isn't installed; logs once at
  startup so it's obvious why the clock isn't moving.
- Sync runs **before** `_mark_fresh()` so a forward-jump in wall-clock
  doesn't make the staleness watchdog immediately mark the source stale.

Privileged surface is intentionally minimal: a single root-owned shell
script (`scripts/aaos-set-time`) that validates the arg is a sane
epoch-seconds integer (1.7e9..4.0e9), plus a sudoers drop-in
(`scripts/aaos-set-time.sudoers`) granting NOPASSWD only for that path.
Install with `bash scripts/install_clock_sync.sh` on the Pi (one-shot).
The installer runs `visudo -c` and rolls back on failure so you can't
brick sudo.

Note: this only fixes the wall-clock offset. Timezone is a separate
config; set it once with `sudo timedatectl set-timezone <your/timezone>`.

## Pi-as-AP transport

In the car the Pi has no internet, and the EX30 head unit may or may
not be on the same network as the Pi. Solution: the Pi runs a WiFi AP
on `wlan0`, the head unit joins it, and the bridge socket targets the
Pi's AP IP. OBD link is Bluetooth so the WiFi radio is free.

`scripts/install_pi_ap.sh` — one-shot installer:
- Creates a NetworkManager connection `ex30-pi-ap` (idempotent: drops
  any prior version first).
- AP mode on `wlan0`, channel 6 / 2.4 GHz, WPA2-PSK.
- SSID defaults to `ex30-pi` (override with `AP_SSID=…`). `AP_PASS` is
  required and has no default — it must be ≥ 8 chars. Shipping a
  passphrase here would be no protection: anyone in range who joins the
  AP can reach the bridge port and feed the display fabricated values.
- Static address `192.168.4.1/24`, `ipv4.method shared` so
  NetworkManager's built-in dnsmasq hands out DHCP leases to clients.
  IPv6 ignored. No internet upstream — DNS is dnsmasq-local-only.
- Sets the WiFi regulatory domain (`AP_COUNTRY=…`, required — it's
  per-country) so the radio is allowed to TX. Unmasks `wpa_supplicant`
  if it was masked — NM uses it for AP-mode auth.
- `connection.autoconnect-priority 100` so the AP wins over any
  remembered client SSIDs at boot.

The companion app's `BridgeClient` binds its socket to the WiFi
`Network` via `ConnectivityManager.registerNetworkCallback`, so the
bridge frames flow over WiFi even when the head unit treats cellular as
its default route and Android marks the internet-less SSID as "not
validated."

If the head unit ever refuses to keep an internet-less network up
despite the user tapping "Stay connected," the fallback would be a tiny
captive-portal responder on the Pi answering `GET /generate_204` with
`204 No Content`. Not implemented — it has not been needed in practice.

## Tests

- `tests/test_aaos_bridge.py` — end-to-end socket tests:
  whitelist filtering, JSON tolerance, log file written, stale flag,
  drop-old-on-new connection, canonical alias writes with unit conversion.
- `tests/test_aaos_obd_priority.py` — verifies the poller suppresses
  AAOS-covered fields when fresh and resumes when stale.
- `tests/test_clock_sync.py` — drift threshold, cooldown, bogus-ts
  rejection, helper-failure handling, disabled-when-missing.
