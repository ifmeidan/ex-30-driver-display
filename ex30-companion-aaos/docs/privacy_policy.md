# Privacy Policy — EX30 Companion

**Last updated**: 19 July 2026 · **Applies to**: app version 1.0.0 and later

EX30 Companion ("the app") is a free, open-source companion app for the
Raspberry Pi EX30 driver display project. This policy describes what data the
app accesses, what it does with that data, and what it does not do.

## Who provides the app

EX30 Companion is published on Google Play by an individual developer as a
hobby / open-source project. There is no operating company behind it and no
commercial use of any data the app touches.

## What data the app accesses

While running on a Volvo EX30 head unit, the app subscribes to a fixed list of
vehicle properties exposed by Android Automotive's `CarPropertyManager` API.
The complete list is published in
[`docs/properties.md`](https://github.com/ifmeidan/ex-30-driver-display/blob/main/ex30-companion-aaos/docs/properties.md)
and includes:

- Vehicle speed
- Gear position (P / R / N / D)
- Parking brake state
- Outside temperature, night-mode flag
- EV battery level (state of charge), battery capacity, instantaneous charge
  rate, range remaining
- Charge port open / connected state
- Ignition state

The app **does not** access:

- Location, GPS, route, or destination data
- Microphone, camera, contacts, calendar, SMS, call log
- Any account / identity / advertising identifier
- The Vehicle Identification Number (VIN) or any other identifier that could
  be linked to a specific car or owner

## What the app does with that data

The app forwards the subscribed property values, in real time, **only to a
local network address that the user types into the app's settings screen.**
The intended target is the user's own Raspberry Pi running the open-source
[ex-30-driver-display](https://github.com/ifmeidan/ex-30-driver-display)
software.

- The destination address is user-controlled and stored on the device.
- The app refuses to send to internet addresses: before every connection
  attempt it checks that the configured destination resolves to a
  private-range (local network) address, and does nothing otherwise.
- The transport is plain TCP on port 7878 (line-delimited JSON).
- No data is sent to any server operated by the app developer or any third
  party. The source code contains no analytics SDK, no ad SDK, no crash
  reporter, and no telemetry of any kind.
- The app stores no logs of vehicle data on the device. Any logging happens
  on the user's own Raspberry Pi, under the user's control.

## What the app does **not** do

- It is **read-only**: the app never calls any property write API on the
  vehicle. This is enforced by a build-time lint rule that fails the build if
  any vehicle write call appears in the source. See
  [`app/build.gradle.kts`](https://github.com/ifmeidan/ex-30-driver-display/blob/main/ex30-companion-aaos/app/build.gradle.kts).
- It does not send commands to any vehicle subsystem.
- It does not show changing data while the vehicle is in motion. The live
  property list is gated behind `PARKING_BRAKE_ON == true` or
  `GEAR_SELECTION == PARK`.
- It does not communicate with any internet service. The only network calls
  are TCP connections to the user-supplied local address.

## Permissions

The app requests the following Android permissions and uses each only for the
purpose stated:

| Permission | Why |
|---|---|
| `android.car.permission.CAR_POWERTRAIN` | Read gear position, parking brake. |
| `android.car.permission.CAR_EXTERIOR_ENVIRONMENT` | Read outside temperature, night mode. |
| `android.car.permission.CAR_ENERGY_PORTS` | Read charge port open / connected state. |
| `android.car.permission.CAR_ENERGY` | Read battery level, charge rate, range. |
| `android.car.permission.CAR_SPEED` | Read vehicle speed. |
| `android.car.permission.CAR_INFO` | Read battery pack capacity (used to compute SoC %). |
| `android.permission.INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` | Open the TCP connection to the user-supplied local address; bind that connection to the WiFi network rather than cellular. |
| `android.permission.CHANGE_NETWORK_STATE` | Request and hold the Pi's internet-less WiFi network so Android keeps it usable for the bridge. |
| `android.permission.FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_CONNECTED_DEVICE`, `POST_NOTIFICATIONS` | Keep the bridge running while the app is in the background; show an ongoing notification so the user can see and stop it. |
| `android.permission.RECEIVE_BOOT_COMPLETED` | Restart the bridge automatically when the head unit boots, so the display works without opening the app each trip. |

## Data retention and deletion

The app stores only the user-typed Pi host address in SharedPreferences. It
can be cleared by uninstalling the app or by overwriting the value in the
app's main screen.

Vehicle telemetry is never retained on the device. It is forwarded to the
user-supplied local address and discarded.

## Children

The app is not directed at children and collects no personal information.

## Changes to this policy

If this policy changes, the new version will be posted at the same URL with
an updated "Last updated" date. The git history of this repository is the
authoritative changelog.

## Contact

Issues and questions: <https://github.com/ifmeidan/ex-30-driver-display/issues>
