# Privacy Policy — EX30 Companion

**Last updated**: 28 November 2026

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

- GPS coordinates or location history
- Microphone, camera, contacts, calendar, SMS, call log
- Any account / identity / advertising identifier
- The Vehicle Identification Number (VIN) or any other identifier that could
  be linked to a specific car or owner

### Optional turn-by-turn maneuver forwarding (experimental, off by default)

By default (`Nav: OFF`), the app does not read any notifications. If the user
explicitly enables the in-app `Nav` toggle and grants Android Notification
Access to the app in system settings:

- The app transiently inspects ongoing navigation guidance notifications from
  Google Maps (`com.google.android.apps.maps`) in memory. It also checks a final
  update to the tracked notification for an explicit arrival.
- Only four sanitized fields are extracted and forwarded to the local receiver:
  the canonical next-maneuver direction token (`turn_left`, `turn_right`,
  `roundabout`, etc.), the next-maneuver distance (e.g. `"300 m"`), the
  roundabout exit number (`1..9`), and the arrival/destination-side token
  (`destination`, `destination_left`, `destination_right`).
- Street names, destination names/addresses, ETA, and raw notification text are
  discarded immediately in memory and are **never** retained, transmitted on the
  wire, or written to Pi disk logs.

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
- The app stores no logs of vehicle data on the device. A developer can
  explicitly enable the `Ex30Nav` debug log tag to see the four sanitized
  navigation fields in Android's diagnostic log.

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
| `android.permission.INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` | Check whether WiFi is enabled and open the bridge socket on WiFi to the user's local receiver. |
| `android.permission.CHANGE_NETWORK_STATE` | Keep a Wi-Fi network available for the bridge. |
| `ACCESS_COARSE_LOCATION`, `ACCESS_FINE_LOCATION`, `ACCESS_BACKGROUND_LOCATION` *(optional, named Wi-Fi profiles)* | Android gates the connected network name behind precise location access. Companion compares that name with the configured Pi network, including while the activity is closed. It does not request geographic coordinates. |
| `android.permission.FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_CONNECTED_DEVICE`, `POST_NOTIFICATIONS` | Keep the bridge running while the app is in the background; show an ongoing notification so the user can see and stop it. |
| `android.permission.RECEIVE_BOOT_COMPLETED` | Restart the bridge automatically when the head unit boots, so the display works without opening the app each trip. |
| `android.permission.BIND_NOTIFICATION_LISTENER_SERVICE` *(optional, user-granted in Settings)* | Used only when the user enables the `Nav` opt-in toggle, to read Google Maps guidance and its final arrival update in memory. |

## Data retention and deletion

The app stores the Pi host address, optional WiFi network name (SSID) and
navigation opt-in on the head unit. For a WPA2 access point, it also stores the
passphrase encrypted with a key held in Android Keystore. Android uses the name
and passphrase to save the selected network when the driver approves during
setup. Wi-Fi credentials stay out of bridge messages. Clearing the Wi-Fi name
and saving removes the app's copy. Clearing app storage or uninstalling the
app removes its local settings. To remove the system's saved network, choose
**Forget** in the head unit's Wi-Fi settings.

The app keeps vehicle telemetry and navigation cues in memory for forwarding
to the user-supplied local address. If a developer explicitly enables the
`Ex30Nav` debug tag, sanitized cues and notification field availability can
also appear in the local Android diagnostic log.

## Children

The app is not directed at children and collects no personal information.

## Changes to this policy

If this policy changes, the new version will be posted at the same URL with
an updated "Last updated" date. The git history of this repository is the
authoritative changelog.

## Contact

Issues and questions: <https://github.com/ifmeidan/ex-30-driver-display/issues>
