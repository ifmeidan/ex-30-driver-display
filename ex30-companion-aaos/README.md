# EX30 Companion — AAOS app

The Android Automotive OS (AAOS) half of the
[EX30 driver display project](../README.md): an app that runs on the
Volvo EX30's head unit, subscribes to a small read-only set of vehicle
properties through the public `android.car` API, and streams them over
the car's WiFi to the Raspberry Pi display
([`../ex30-companion-runner/`](../ex30-companion-runner/)).

This folder is published primarily as a **methodology reference** — so
you can see exactly how the app was built, which properties the retail
EX30 actually grants, and how the bridge works. You don't need to build
it yourself: the app is distributed through Google Play (closed beta
today, public once the conditions are met — see the
[setup guide](../SETUP.md)).

## What it does

- Subscribes to 13 vehicle properties (speed, SoC, battery power, range,
  gear, ignition, parking brake, charge port, ambient temperature, night
  mode, pack capacity) via `CarPropertyManager`. The catalog with
  per-property status lives in
  [`docs/properties.md`](docs/properties.md).
- Streams them as line-delimited JSON over TCP to the Pi at `:7878` —
  wire protocol in [`pi_bridge/protocol.md`](pi_bridge/protocol.md).
- Pins its socket to the Pi's internet-less WiFi AP so frames flow even
  while the head unit prefers cellular.
- Goes quiet when you leave the car (ignition/screen-off detection) so
  it never drains the 12 V battery or holds the vehicle network awake;
  stays up while charging so the Pi can render the charging screen.
- **Read-only by construction**: no `setProperty` anywhere, enforced by
  a build-time source scan — see
  [`docs/safety_warranty.md`](docs/safety_warranty.md).

## Layout

| Path | Purpose |
|---|---|
| `app/` | The Android Studio project (Kotlin, package `com.ex30companion`) |
| `docs/properties.md` | Property catalog — what the retail EX30 grants, blocks, and why |
| `docs/permissions.md` | Per-property permission tier + how grants work on AAOS |
| `docs/safety_warranty.md` | Read-only invariants and how they are enforced |
| `docs/privacy_policy.md` | The app's privacy policy |
| `pi_bridge/protocol.md` | Wire protocol between the app and the Pi receiver |

## Building (optional)

Standard Android Gradle project: open in Android Studio (AGP 9.x,
JDK 17) and assemble a debug build.

SDK levels, decoded: the EX30's head unit runs **Android Automotive 12L
(API 32)** — that's `minSdk 32`, and the platform everything in this
repository was verified on. `targetSdk 34` exists only because Google
Play refuses uploads targeting anything older; nothing in the app
depends on API 34 behaviour. `compileSdk 35` is just the build-against
SDK.

```bash
./gradlew assembleDebug     # debug APK
./gradlew bundleRelease     # release AAB (Play upload format)
```

### Release signing

Release builds look for `keystore.properties` in this directory. It and
the keystore it points at are **git-ignored** — they never leave the
maintainer's machine, and this repository ships neither. Without them,
`bundleRelease` still builds; the output is simply unsigned.

To sign your own builds, create your own upload key and a matching
`keystore.properties` (values yours, keys exactly these):

```properties
storeFile=upload-keystore.jks
storePassword=…
keyAlias=…
keyPassword=…
```

`storeFile` is resolved relative to this directory. Google Play App
Signing then holds the real app-signing key; the upload key only proves
who is uploading, so keep it backed up somewhere safe — losing it means
asking Google to reset the upload key.
