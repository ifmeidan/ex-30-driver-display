# Safety and Warranty

## What people mean by "warranty" here

There is no warranty risk from installing a signed Play Store app on an EX30.
Volvo cannot detect installed apps, and Play Store apps run in the standard
Android sandbox — they cannot modify firmware, ECU calibration, or any
safety-critical subsystem. The app uses public framework APIs only.

The **real** risk is writing to vehicle properties — `setProperty` on certain
properties (HVAC, mirrors, locks, lights) does change vehicle state and could,
in principle, surprise a driver. This app does not write. Ever.

## Hard invariants

These are **invariants**, not preferences. The `verifyReadOnly` build
guard enforces #1 on every build; the rest are enforced by the concrete,
runnable checks listed below.

1. **No `setProperty` calls.** The app uses `getProperty`, `registerCallback`,
   `unregisterCallback` only.
2. **No `CONTROL_*` or `WRITE_*` permissions in the manifest.**
3. **No `VENDOR_*` properties.** AOSP-defined property IDs only. Vendor properties
   are OEM-specific, undocumented, and a moving target — exactly the surface where
   accidental writes or undefined behavior live.
4. **No reflection, no system_server access, no shell.** The app operates entirely
   through the public `android.car.*` API.
5. **No background data exfiltration.** The bridge connects only to
   private-range local addresses (RFC1918: `10.0.0.0/8`, `172.16.0.0/12`,
   `192.168.0.0/16` — plus loopback for bench tests). `BridgeClient`
   checks the resolved address before every connect attempt and refuses
   anything else, including hosts that don't resolve.

## How the invariants are enforced

- **Build-time guard** (`verifyReadOnly` Gradle task, wired into `preBuild`
  in `app/build.gradle.kts`): fails the build if `setProperty(`,
  `CarPropertyManager.set`, or any `.set*Property(` call appears anywhere
  in `app/src/`.
- **Manifest allowlist**: the manifest declares only the read permissions
  documented in `permissions.md` — no `CONTROL_*`/`WRITE_*` entries.
- **Property allowlist**: every `VehiclePropertyIds` constant used in the
  source lives in `PropertyCatalog.kt`, the single source of truth,
  documented per-property in `properties.md`.
- **Bridge target check**: `BridgeClient` refuses to connect to any
  non-RFC1918 host.

## Operational safety

- Live data view in the app UI is **parked-only** (driver-distraction rule).
- App requests permissions at launch, never silently. If user denies a permission,
  the corresponding fields are reported as "denied" — no shadow workarounds.
- Crash → reconnect with backoff, no state mutation.
- The Pi bridge is one-way (car → Pi). The Pi cannot push commands or property
  writes back. If we ever want bidirectional, that's a separate review with the
  user — not a quiet upgrade.

## What a reviewer (Volvo, Google, or future-us) can verify in 5 minutes

1. `grep -r "setProperty" app/src/` → empty.
2. `grep -r "VENDOR_" app/src/` → empty.
3. `cat app/src/main/AndroidManifest.xml` → permissions match `permissions.md`.
4. `grep -n "isSiteLocalAddress" app/src/main/java/com/ex30companion/BridgeClient.kt`
   → the RFC1918 host check, right before the connect call.
5. `./gradlew verifyReadOnly` → passes (it also runs automatically on
   every build via `preBuild`).

If any of these checks fail in a future change, the change is wrong, regardless
of what it claims to do.
