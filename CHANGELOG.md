# EX30 Driver Display version notes

Each entry covers one commit on `main` since the first closed-test snapshot. Under **AAOS**, find the Companion app version, its Android build number and the Android version it targets. All listed builds support Android Automotive 12L (API 32) and later. Under **Raspberry Pi runner**, use the commit link to identify the corresponding Pi software. Dates are commit dates. Google Play rollout dates appear where confirmed.

To check your installed Companion version, open **Settings → Apps → EX30 Companion** on the head unit.

## 27 November 2026 · Version history published on GitHub

[Documentation change #10](https://github.com/ifmeidan/ex-30-driver-display/pull/10)

### AAOS · Companion 1.0.7 (build 21) · targets Android 15

The app README now links to the closed-test history. This documentation update keeps the same Companion build.

### Raspberry Pi runner

Pi owners can now see from the project README which updates require new runner software. This entry changes documentation only.

## 27 November 2026 · Reconnect to the display after a restart

[Commit 7c2a998](https://github.com/ifmeidan/ex-30-driver-display/commit/7c2a9988320ae1f1d6368720279ed545daabe902)

### AAOS · Companion 1.0.7 (build 21) · targets Android 15

- **One-time setup while parked.** Enter the Pi host address (`192.168.4.1` for the default Pi setup), tap **Wi-Fi**, and enter the receiver’s exact network name. For a WPA2 network, enter its password. Tap **Save and connect** and approve Android’s connection request. The head unit saves the network name locally and encrypts the password with Android Keystore.
- **On the next drive.** Companion requests the saved network when the head unit starts or wakes. If a previously connected receiver takes longer to power up, Companion retries up to three times. If Wi-Fi is off, tap **Connect → Open Wi-Fi settings** to turn it on. If you dismiss the network request, tap **Connect** when you want to try again.
- **Upgrading an existing setup.** Your saved Pi address carries over. Leave the Wi-Fi name empty to keep joining the network in car settings. You can switch to automatic reconnection later by entering the network name and password. [Setup guide](SETUP.md#7-configure-the-app-one-time-while-parked)
- **Tested on Android Automotive 15.** The test receiver received fresh vehicle data after a full emulator reboot, after switching Wi-Fi off and on, and when the receiver’s access point came online 65 seconds after ignition. A host-only setup also continued sending data after an upgrade.

### Raspberry Pi runner

The Pi access-point installer now prints the app setup steps, including the configured SSID and Pi address. The Wi-Fi fix runs on the head unit. An existing Pi access point and receiver can pick up the reconnected app without reinstalling the runner.

## 26 November 2026 · Experimental Google Maps guidance forwarding

[Commit 523dd2b](https://github.com/ifmeidan/ex-30-driver-display/commit/523dd2b0ac4d3e9a9d91b8cca2d8122cebfac672)

### AAOS · Companion 1.0.6 (build 20) · targets Android 15

The source still identified as 1.0.6 (build 20) when this change landed, after the October rollout. The 1.0.7 build includes the feature. Companion can forward the next maneuver and distance from active Google Maps guidance, plus the roundabout exit or arrival side when available.

Forwarding defaults to off. Tap **Nav: OFF** and grant Android **Notification Access** to enable it. Tap **Nav: ON** to turn it off again. Street names, destinations and raw notification text stay out of the messages. [Navigation protocol](ex30-companion-aaos/pi_bridge/protocol.md#nav--experimental-opt-in-turn-by-turn-maneuver-update)

### Raspberry Pi runner

Update the runner to this commit or later and start it with `--aaos-nav` to receive cues. The receiver keeps the latest cue in memory for custom displays and clears it after 15 seconds without a refresh. For a parked test, add `--aaos-nav-diag` to see the maneuver and distance in the log. The stock dashboard has no turn-arrow view in this build.

## 29 September 2026 · Restore the connection controls on Android 15

[Commit be10605](https://github.com/ifmeidan/ex-30-driver-display/commit/be10605dd7d427c99effbb98255ca3e31bac33aa)

### AAOS · Companion 1.0.6 (build 20) · targets Android 15

On some Android 15 head units, the vehicle-data list appeared but the Pi host field and **Connect** button were hidden. This update restored the connection row so drivers could enter the receiver’s address again. Joining the receiver’s Wi-Fi was still a manual step in this version.

The maintainer announced the 1.0.6 rollout on 3 October. On 12 October, a tester confirmed the connection bar worked on their head unit and reported having to rejoin their ESP32 network after each restart. That second report led to the Wi-Fi change in 1.0.7. [Rollout announcement](https://github.com/ifmeidan/ex-30-driver-display/issues/2#issuecomment-5972371814) · [Tester’s report](https://github.com/ifmeidan/ex-30-driver-display/issues/2#issuecomment-6130456854)

### Raspberry Pi runner

No runner files changed. The connection-bar fix only requires the head-unit app update.

## 25 August 2026 · Target Android Automotive 15

[Commit 0b710ae](https://github.com/ifmeidan/ex-30-driver-display/commit/0b710ae387d4769d8ac2d84c451411dcc728db3d)

### AAOS · Companion 1.0.5 (build 19) · targets Android 15

Companion began targeting Android 15 (API 35) and continued to support Android Automotive 12L (API 32). The update used the existing Pi address and manual Wi-Fi setup. A closed-test participant later confirmed build 19 was installed on their Android 15 head unit and reported the missing connection controls fixed in 1.0.6. [Android 15 report](https://github.com/ifmeidan/ex-30-driver-display/issues/2)

### Raspberry Pi runner

No runner files changed. Existing receivers can keep using the same Wi-Fi and bridge settings.

## 20 July 2026 · Closed-test integration with the car launcher

[Commit 3511728](https://github.com/ifmeidan/ex-30-driver-display/commit/3511728a35d2d9233efb5b096f591ec2112f4908)

### AAOS · Companion 1.0.4 (build 17) · targets Android 14

Companion added the Android for Cars app category and template used for its Automotive Google Play listing. If the car opens a screen directing you to the home screen, open **EX30 Companion** from the car launcher to reach the Pi connection controls and vehicle data.

### Raspberry Pi runner

No runner files changed. The launcher integration applies to the app installed on the car.

## 19 July 2026 · First repository snapshot of the closed test

[Commit 1e47fe2](https://github.com/ifmeidan/ex-30-driver-display/commit/1e47fe2ea9c33c9ad78ed885d67fb95ceea5111e)

### AAOS · Companion 1.0.2 (build 15) · targets Android 14

The setup guide at this point identified **1.0.1** as the version available to closed testers. The first commit already contained the **1.0.2** source. [Original closed-test instructions](https://github.com/ifmeidan/ex-30-driver-display/blob/1e47fe2ea9c33c9ad78ed885d67fb95ceea5111e/README.md#getting-the-aaos-app)

Testers joined the Pi’s Wi-Fi in car settings, entered its address in Companion and tapped **Connect**. The app remembered the address and streamed read-only vehicle data, including display speed, battery level, range, gear and charging state. Drivers could inspect live readings while parked. The background service could start on boot, pause when the driver left and keep the bridge active while the charge port was connected.

### Raspberry Pi runner

The first snapshot included the Wi-Fi receiver and the Bluetooth OBD2 reader. The drive screen combined the car’s display speed, range and gear with OBD2 readings such as brake pressure and battery temperature. It supported day and night themes, a trip computer and a charging screen with charging power and a curve. If the head-unit feed went stale, the speed readout cleared to avoid leaving an old speed on screen.
