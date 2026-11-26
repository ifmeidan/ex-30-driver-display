# EX30 Companion version notes

These notes follow each change on `main` since the first public snapshot of the closed test. Each entry gives the commit date and the Companion version and build number in the source. Google Play rollout dates are included where confirmed. On the head unit, open **Settings → Apps → EX30 Companion** to see your installed version.

## 1.0.7 · Reconnect to the display after a restart

27 November 2026 · Build 21 · [Commit 7c2a998](https://github.com/ifmeidan/ex-30-driver-display/commit/7c2a9988320ae1f1d6368720279ed545daabe902)

- **One-time setup while parked.** Enter the Pi host address (`192.168.4.1` for the default Pi setup), tap **Wi-Fi**, and enter the receiver’s exact network name. For a WPA2 network, enter its password. Tap **Save and connect** and approve Android’s connection request. The head unit saves the network name locally and encrypts the password with Android Keystore.
- **On the next drive.** Companion requests the saved network when the head unit starts or wakes. If a previously connected receiver takes longer to power up, Companion retries up to three times. If Wi-Fi is off, tap **Connect → Open Wi-Fi settings** to turn it on. If you dismiss the network request, tap **Connect** when you want to try again.
- **Upgrading an existing setup.** Your saved Pi address carries over. Leave the Wi-Fi name empty to keep joining the network in car settings. You can switch to automatic reconnection later by entering the network name and password. [Setup guide](SETUP.md#7-configure-the-app-one-time-while-parked)
- **Tested on Android Automotive 15.** The test receiver received fresh vehicle data after a full emulator reboot, after switching Wi-Fi off and on, and when the receiver’s access point came online 65 seconds after ignition. A host-only setup also continued sending data after an upgrade.

## 1.0.6 · Experimental Google Maps guidance forwarding

26 November 2026 · Build 20 · [Commit 523dd2b](https://github.com/ifmeidan/ex-30-driver-display/commit/523dd2b0ac4d3e9a9d91b8cca2d8122cebfac672)

This change landed on `main` after the October 1.0.6 rollout and is included in the 1.0.7 build. It lets a compatible receiver read the next maneuver and distance from an active Google Maps guidance notification, plus the roundabout exit or arrival side when available.

- To try it, tap **Nav: OFF** in Companion, grant Android **Notification Access**, and start the updated Pi receiver with `--aaos-nav`. Both sides default to off. Tap **Nav: ON** to turn forwarding off again.
- The stock Pi receiver exposes the cues in memory for custom displays. To inspect them during setup, add `--aaos-nav-diag`. The stock dashboard has no turn-arrow view in this build. Forwarded messages omit street names, destination names and notification text. [Navigation protocol](ex30-companion-aaos/pi_bridge/protocol.md#nav--experimental-opt-in-turn-by-turn-maneuver-update)

## 1.0.6 · Restore the connection controls on Android 15

29 September 2026 · Build 20 · [Commit be10605](https://github.com/ifmeidan/ex-30-driver-display/commit/be10605dd7d427c99effbb98255ca3e31bac33aa)

On some Android 15 head units, the vehicle-data list appeared but the Pi host field and **Connect** button were hidden. This update restored the connection row so drivers could enter the receiver’s address again. Joining the receiver’s Wi-Fi was still a manual step in this version.

The maintainer announced the 1.0.6 rollout on 3 October. On 12 October, a tester confirmed the connection bar worked on their head unit and reported having to rejoin their ESP32 network after each restart. That second report led to the Wi-Fi change in 1.0.7. [Rollout announcement](https://github.com/ifmeidan/ex-30-driver-display/issues/2#issuecomment-5972371814) · [Tester’s report](https://github.com/ifmeidan/ex-30-driver-display/issues/2#issuecomment-6130456854)

## 1.0.5 · Target Android Automotive 15

25 August 2026 · Build 19 · [Commit 0b710ae](https://github.com/ifmeidan/ex-30-driver-display/commit/0b710ae387d4769d8ac2d84c451411dcc728db3d)

Companion began targeting Android 15 (API 35) and continued to support Android Automotive 12L (API 32). The update used the existing Pi address and manual Wi-Fi setup. A closed-test participant later confirmed build 19 was installed on their Android 15 head unit and reported the missing connection controls fixed in 1.0.6. [Android 15 report](https://github.com/ifmeidan/ex-30-driver-display/issues/2)

## 1.0.4 · Closed-test integration with the car launcher

20 July 2026 · Build 17 · [Commit 3511728](https://github.com/ifmeidan/ex-30-driver-display/commit/3511728a35d2d9233efb5b096f591ec2112f4908)

Companion added the Android for Cars app category and template used for its Automotive Google Play listing. If the car opens a screen directing you to the home screen, open **EX30 Companion** from the car launcher to reach the Pi connection controls and vehicle data.

## 1.0.2 · First repository snapshot of the closed test

19 July 2026 · Build 15 · [Commit 1e47fe2](https://github.com/ifmeidan/ex-30-driver-display/commit/1e47fe2ea9c33c9ad78ed885d67fb95ceea5111e)

The setup guide at this point identified **1.0.1** as the version available to closed testers. The first commit already contained the **1.0.2** source. [Original closed-test instructions](https://github.com/ifmeidan/ex-30-driver-display/blob/1e47fe2ea9c33c9ad78ed885d67fb95ceea5111e/README.md#getting-the-aaos-app)

- Testers joined the Pi’s Wi-Fi in car settings, entered its address in Companion and tapped **Connect**. The app remembered the address and streamed read-only vehicle data, including display speed, battery level, range, gear and charging state, to the Pi.
- The Raspberry Pi combined this feed with OBD2 readings for the drive and charging screens. The head-unit app showed its live property list while parked. Its background service could resume on boot, pause the bridge when the driver left and keep it active while the charge port was connected.
