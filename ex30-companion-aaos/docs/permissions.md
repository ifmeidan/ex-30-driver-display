# Permissions

**Rule**: this app declares ONLY read permissions. No `CONTROL_*`, no `WRITE_*`,
no signature/privileged permissions. Enforced by the `verifyReadOnly` build guard + manifest review.

## Manifest declaration

The car-permission strings below correspond to the `Car.PERMISSION_*`
constants used in `properties.md`; the snippet mirrors the app's actual
`AndroidManifest.xml` (keep the two in sync when either changes).

```xml
<!-- Normal — auto-granted at install -->
<uses-permission android:name="android.car.permission.CAR_POWERTRAIN" />
<uses-permission android:name="android.car.permission.CAR_EXTERIOR_ENVIRONMENT" />
<uses-permission android:name="android.car.permission.CAR_ENERGY_PORTS" />
<uses-permission android:name="android.car.permission.CAR_INFO" />

<!-- Dangerous — runtime prompt -->
<uses-permission android:name="android.car.permission.CAR_ENERGY" />
<uses-permission android:name="android.car.permission.CAR_SPEED" />

<!-- Network for the Pi bridge (all Normal tier). CHANGE_NETWORK_STATE is
     what lets BridgeClient requestNetwork()-hold the internet-less Pi AP. -->
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
<uses-permission android:name="android.permission.ACCESS_WIFI_STATE" />
<uses-permission android:name="android.permission.CHANGE_NETWORK_STATE" />

<!-- Foreground bridge service + its mandatory status notification -->
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE" />
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />

<!-- Auto-start the bridge on head-unit boot -->
<uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />

<!-- AAOS gating -->
<uses-feature android:name="android.hardware.type.automotive" android:required="true" />
```

## Permission tier reference

| Permission | Tier | Granted by | Covers |
|---|---|---|---|
| `CAR_POWERTRAIN` | Normal | install | gear (current + selection), parking brake, ignition, EV regen level + stopping mode |
| `CAR_EXTERIOR_ENVIRONMENT` | Normal | install | outside temp, night mode |
| `CAR_ENERGY_PORTS` | Normal | install | charge port connected / open |
| `CAR_ENERGY` | Dangerous | runtime | SoC, range, battery temp/capacity, charge state/time/limits/draw, regen state |
| `CAR_SPEED` | Dangerous | runtime | speed (raw + display-calibrated) |

## Runtime permission request flow

1. App launch → check `ContextCompat.checkSelfPermission` for each Dangerous one.
2. If any missing, route to a `RationaleActivity` that explains:
   - What we read (broadly: vehicle telemetry — speed, gear, EV state, etc.)
   - That we **never** write to the car
   - That data leaves the car only over a local-network connection to the user's Pi
3. `ActivityCompat.requestPermissions` for all missing in one prompt. AAOS shows
   one consolidated dialog per permission group, so the actual prompt count is
   smaller than the manifest list.
4. If denied: feature degrades. App continues with whatever was granted; the raw
   property list shows "permission denied" rows for the rest. Do not nag.