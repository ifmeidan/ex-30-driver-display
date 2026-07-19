# Property Catalog 

**Reference**: https://developer.android.com/reference/android/car/VehiclePropertyIds

The AAOS app reads every Keep row marked `keep-confirmed` and forwards values
over the bridge. Whether the Pi UI uses an AAOS-sourced field or its OBD2
equivalent is a Pi-side decision in `ex30-companion-runner/`, not an AAOS-side one.

## Keep — Normal tier (auto-granted at install)

CURRENT_GEAR                    | Car.PERMISSION_POWERTRAIN            | keep-confirmed
ENV_OUTSIDE_TEMPERATURE         | Car.PERMISSION_EXTERIOR_ENVIRONMENT  | keep-confirmed
INFO_EV_BATTERY_CAPACITY        | Car.PERMISSION_CAR_INFO              | keep-confirmed
EV_CHARGE_PORT_CONNECTED        | Car.PERMISSION_ENERGY_PORTS          | keep-confirmed
GEAR_SELECTION                  | Car.PERMISSION_POWERTRAIN            | keep-confirmed
IGNITION_STATE                  | Car.PERMISSION_POWERTRAIN            | keep-confirmed
NIGHT_MODE                      | Car.PERMISSION_EXTERIOR_ENVIRONMENT  | keep-confirmed
PARKING_BRAKE_ON                | Car.PERMISSION_POWERTRAIN            | keep-confirmed

## Keep — Dangerous tier (runtime prompt)

EV_BATTERY_INSTANTANEOUS_CHARGE_RATE     | Car.PERMISSION_ENERGY                  | keep-confirmed
EV_BATTERY_LEVEL                         | Car.PERMISSION_ENERGY                  | keep-confirmed
PERF_VEHICLE_SPEED                       | Car.PERMISSION_SPEED                   | keep-confirmed
PERF_VEHICLE_SPEED_DISPLAY               | Car.PERMISSION_SPEED                   | keep-confirmed
RANGE_REMAINING                          | Car.PERMISSION_ENERGY                  | keep-confirmed

## CONTINUOUS sample-rate caps

VHAL `maxSampleRate` is far higher than the bridge needs (SoC reports 100 Hz).
Caps applied client-side in `CarPropertyService.kt#CONTINUOUS_RATE_CAP_HZ`:

| Property | Cap (Hz) | Reasoning |
|---|---|---|
| `PERF_VEHICLE_SPEED` | 10 | smooth speedo on Pi UI |
| `PERF_VEHICLE_SPEED_DISPLAY` | 10 | same |
| `EV_BATTERY_INSTANTANEOUS_CHARGE_RATE` | 30 | throttle/regen power source — 30 Hz so the Pi's power gauge moves smoothly with pedal input; paired with the 33 ms coalescer tick in `BridgeClient` |
| `EV_BATTERY_LEVEL` | 0.5 | SoC moves on the order of minutes |
| `RANGE_REMAINING` | 0.5 | recomputed slowly |
| `ENV_OUTSIDE_TEMPERATURE` | 0.5 | sensor noise dominates above this |

Combined steady-state event rate ≈ 51.5/s, and the 33 ms coalescer batches
them into at most ~30 frames/s on the wire. At well under 1 KB per frame
that's a few tens of KB/s worst case — trivial for the WiFi TCP link (and
still within BT SPP throughput if the serial fallback ever ships).
