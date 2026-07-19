# AAOS ↔ Pi Bridge Protocol

**Version**: 1.1
**Direction**: car → Pi only. Pi never sends to car.
**Transport**: WiFi TCP, line-delimited JSON. BT SPP fallback (later phase) uses
the same JSON framing over the serial stream.

---

## Connection

- Pi runs a TCP server on port **7878** bound to `0.0.0.0`.
- AAOS app connects on app launch and on every network availability change.
- One connection at a time. Server drops the previous connection if a new one
  arrives.
- App reconnects with exponential backoff: 1s, 2s, 4s, 8s, 16s, capped at 30s.
  Backoff resets to 1s after a successful `hello` ack window (≥10s connected).

## Framing

One JSON object per line, terminated by `\n`. UTF-8. Max line length 4 KB. Lines
longer than that are dropped on both sides with a warning log. The sender
guarantees each `state` frame fits — the 13-field cut is well under 1 KB even
with all fields present.

## Backpressure

Sender keeps a bounded send queue (capacity 64 frames). On overflow, the oldest
queued frame is dropped and a `WARN` logged with the dropped frame's `ts`. This
keeps a stalled socket from growing memory unbounded if the Pi disappears
mid-frame and the OS write buffer fills.

## Message types

### `hello` — first message after connect

```json
{
  "type": "hello",
  "ts": 1714723200000,
  "app_version": "1.0.0",
  "schema_version": 1,
  "fields_subscribed": ["speed_aaos", "soc_aaos", "gear", "..."]
}
```

`fields_subscribed` lists the wire field names the app is *currently capable of
emitting* (i.e. property registered successfully and at least one sample seen,
or registered and waiting). Pi can use this to detect coverage drops between
runs (e.g. a Volvo OTA that revokes a permission).

VIN/make/model are not included — the v1 cut does not include
`CAR_IDENTIFICATION` and adding it just for `hello` is out of scope.

### `state` — property update batch

Emitted on every 100ms tick that has at least one field changed since the last
`state` frame. Coalesces multiple updates within the tick window: the latest
value per field wins. If nothing changed in the tick, no `state` is sent.

```json
{
  "type": "state",
  "ts": 1714723200123,
  "src": "aaos",
  "fields": {
    "speed_aaos": 13.4,
    "speed_display_aaos": 13.0,
    "soc_aaos": 72.5,
    "gear": "D",
    "gear_selected": "D",
    "parking_brake": false,
    "ignition_state": "ON",
    "charge_port_connected": false
  }
}
```

The `"src": "aaos"` tag is mandatory. Pi receiver uses it to distinguish from
OBD2-sourced updates and to log into `aaos_bridge_YYYYMMDD.jsonl` separately.

Only fields that **changed** since the last `state` frame are included. Pi must
treat absent fields as "unchanged", not "unknown". Initial values arrive in the
first `state` frame after `hello`.

### `heartbeat` — when nothing changed for 5s

```json
{ "type": "heartbeat", "ts": 1714723205000 }
```

If the Pi receives no `state` or `heartbeat` for 15s, it marks AAOS source as
**stale** and the UI greys out AAOS-derived tiles.

### `goodbye` — clean disconnect (best effort)

```json
{ "type": "goodbye", "ts": 1714723210000, "reason": "app_paused" }
```

Reasons: `app_paused`, `app_destroyed`, `network_lost`, `manual_reconnect`,
`parked_ignition_ACC`, `parked_ignition_OFF`, `parked_ignition_LOCK`,
`parked_screen_off`.

---

## Parked mode

The bridge goes quiet when the driver leaves, so the app never holds WiFi
or CPU awake for a locked car — and so the Pi's OBD polling (which the Pi
stops on the same signal) can't keep the vehicle network awake.

EX30 retail VHAL ignition semantics: `ON` = car unlocked/opened, `ACC` = car locked but the
vehicle network still awake, `OFF`/`LOCK` = never delivered before the
head unit suspends. So ACC is the "driver left" edge.

Enter parked when ignition ∈ {ACC, OFF, LOCK}, or — backup, VHAL-glitch
insurance — when the head-unit display turns off with no ignition change
(in that case the app first synthesizes a `state` frame with
`ignition_state: "OFF"` so the Pi still gets an explicit signal).
Exception: while `charge_port_connected` is true the bridge stays up so
the Pi can render the charging screen.

On entering parked, in order: flush the final ignition `state` frame,
send `goodbye` with a `parked_*` reason, close the socket, release the
WiFi network request, and unsubscribe every VHAL property except
IGNITION_STATE and EV_CHARGE_PORT_CONNECTED.

Exit parked on ignition ON/START, screen on, or charge-port connect:
re-request WiFi, reconnect, and reseed the full snapshot (standard
`hello` + seed flow — the Pi needs no new logic beyond reacting to the
ignition field, which it already does).

---

## Field map

These are the only fields the EX30 build grants on API 32. See
`docs/properties.md` for the per-property tier and for why the rest are
SDK- or VHAL-blocked.

The `_aaos` suffix is appended to fields where OBD2 is also a source on the Pi
side, so the Pi can keep both streams distinct and the UI can show divergence
if it occurs. Fields without an OBD2 counterpart use the bare name.

| AAOS property | Wire field | Type | Encoding |
|---|---|---|---|
| `PERF_VEHICLE_SPEED` | `speed_aaos` | float | m/s, signed (negative = reverse) |
| `PERF_VEHICLE_SPEED_DISPLAY` | `speed_display_aaos` | float | m/s, calibrated to dash speedo |
| `EV_BATTERY_LEVEL` | `soc_aaos` | float | Wh; Pi converts to % using pack capacity |
| `EV_BATTERY_INSTANTANEOUS_CHARGE_RATE` | `battery_power_mw` | float | mW, sign-flipped from VHAL: positive = charging/regen, negative = discharging |
| `RANGE_REMAINING` | `range_m_aaos` | float | meters |
| `CURRENT_GEAR` | `gear` | string | `P`, `R`, `N`, `D`, or `UNKNOWN` |
| `GEAR_SELECTION` | `gear_selected` | string | `P`, `R`, `N`, `D`, or `UNKNOWN` |
| `PARKING_BRAKE_ON` | `parking_brake` | bool | — |
| `IGNITION_STATE` | `ignition_state` | string | `LOCK`, `OFF`, `ACC`, `ON`, `START`, `UNKNOWN` |
| `EV_CHARGE_PORT_CONNECTED` | `charge_port_connected` | bool | — |
| `ENV_OUTSIDE_TEMPERATURE` | `ambient_temp_aaos` | float | °C |
| `NIGHT_MODE` | `night_mode` | bool | — |
| `INFO_EV_BATTERY_CAPACITY` | `pack_capacity_wh` | float | Wh, **STATIC** — sent in the first `state` after each connect, never resent unless the head unit reboots into a different car |

Enums come over the wire as strings (the AOSP enum constant name without
prefix), not the int. Easier to debug, no version drift if AOSP renumbers.
`UNKNOWN` covers any int value the AAOS build emits that the app doesn't have
a name for (forward-compatible — Pi doesn't crash on a future enum addition).

`EV_BATTERY_LEVEL` is shipped as raw Wh deliberately. The Pi already converts
OBD2 SoC to a percentage using a configured pack capacity; doing the same
conversion here keeps both sources comparable and lets the Pi own the
calibration.

`pack_capacity_wh` (`INFO_EV_BATTERY_CAPACITY`) lets the Pi auto-calibrate
the SoC % conversion against the actual car instead of a hardcoded LR/SR
guess. The property is STATIC: AAOS reads it once at startup with
`getProperty()` (callbacks don't fire reliably for STATIC properties) and
includes it in the seed snapshot the bridge emits on every connect. The Pi
should treat any nonzero value it sees as authoritative for that session.

---

## Pi receiver responsibilities

- Bind TCP server on `0.0.0.0:7878`.
- One connection at a time; on new connect, drop old.
- Per-line JSON parse; bad lines dropped with `WARNING` log.
- Validate `type`, `ts`, `src`, and (for `state`) `fields` is a dict.
- Log every accepted message to `logs/aaos_bridge_YYYYMMDD.jsonl` on the Pi,
  one line per message.
- `state.fields` → `SharedVehicleData.update(**fields)`. The Pi side is
  responsible for mapping the wire field names onto its internal schema; this
  protocol does not require Pi-side suffix stripping.
- Track last-message timestamp per source; if AAOS goes >15s without a message,
  mark stale and set `vehicle_data.aaos_stale = True`.

## Pi-side wall-clock sync (informative)

The Pi has no RTC battery and is offline outside the car, so it relies on
frame `ts` (epoch ms, `System.currentTimeMillis` on the sender) to discipline
its own system clock. The sender doesn't need to do anything special — just
keep emitting wall-clock `ts` in every frame. See
`ex30-companion-runner/docs/aaos_bridge.md` "Wall-clock sync" for the Pi
implementation. If we ever switch the sender to a monotonic source, the Pi
loses its only time reference, so wall-clock is load-bearing here.

## Security notes

The current build (including the closed beta on Play) ships **plain TCP**
— acceptable because the transport is the user's own Pi AP with no
internet upstream, and the stream is read-only vehicle telemetry. Pick
your own AP passphrase at install; the default documented in this
repository is public knowledge.

Implemented:

- App-side host validation: the app refuses to connect to anything that
  isn't a private-range (RFC1918) or loopback address — checked in
  `BridgeClient` before every connect (see `docs/safety_warranty.md`).

Deferred until a future release:

- TLS with cert pinning. Cert is generated on the Pi at first boot, fingerprint
  shown on the Pi's display, user enters it once in the AAOS app's pairing screen.
- Replay protection: monotonic `ts` enforced on receiver.
