# Setup Guide

End-to-end instructions to build the EX30 driver display: flash and
configure the Raspberry Pi, pair the OBD2 adapter, bring up the WiFi
bridge, and configure the AAOS companion app on the car.

Expect roughly an afternoon for the bench setup and an hour in the car.

---

## 1. What you need

| Part | Notes |
|---|---|
| Raspberry Pi 4 (2 GB or more) | The display computer |
| Waveshare 8.8″ 1920×480 IPS display | Connects via micro-HDMI + USB |
| vLinker MC+ **Bluetooth** OBD2 adapter | Other ELM327-compatibles may work, but all timing was tuned on this one |
| High-endurance microSD (16 GB+) | Cabin temperatures kill cheap cards |
| Passive aluminum Pi case | Fanless cooling |
| 12 V→5 V step-down converter, 3 A, USB-C (dashcam hardwire kit) | Powers Pi + display from an **ignition-switched** fuse |
| FPC micro-HDMI ribbon cable, right-angle USB-A→USB-C cable | For a clean install behind the trim |
| A Volvo EX30 | Any trim; the head unit runs Android Automotive 12L |

You'll also need a computer to flash the SD card, and (only during
setup) a way to SSH into the Pi — Ethernet or your home WiFi.

---

## 2. Flash the Raspberry Pi

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose **Raspberry Pi OS Lite (64-bit)**. Current images are Debian 13
   (Trixie, Python 3.13) — that's what this project is tested on.
3. In the Imager's settings (gear icon), before writing:
   - Set a **hostname**, your **username and password** (pick your own —
     nothing in this project assumes a specific user),
   - **Enable SSH**,
   - Configure WiFi (temporary — just for setup). **The computer you SSH
     from must be on this same network.** A phone hotspot works well: put
     both the Pi and your laptop on it, and the pair stays reachable even
     out at the car.
4. Write the card, boot the Pi, and confirm you can SSH in:

   ```bash
   ssh <your-user>@<pi-address>
   ```

   If you've flashed this Pi before, SSH may refuse with a host-key
   warning — the fresh install has a new key on the same address. Clear
   the old entry and retry:

   ```bash
   ssh-keygen -R <pi-address>
   ```

---

## 3. Install the runner

Clone this repository onto the Pi and run the bootstrap script.

> **Do this step at the car, with the Pi.** The vLinker is powered by the
> OBD2 port, so it only exists on the air when the car is awake, and
> Bluetooth range is only about 10 m. The Pi has to be sitting in (or
> right next to) the car for pairing to work — a USB power bank and a
> phone hotspot are enough. Pairing from indoors while the car is on the
> driveway will not work.

You need your vLinker's Bluetooth MAC address — plug the vLinker into the
car's OBD2 port (under the steering column), wake the car, then on the Pi:

```bash
bluetoothctl
# power on
# scan on            ← wait for the vLinker entries to appear
# scan off / exit
```

The adapter advertises **two** interfaces, which differ only in the first
octet:

```text
34:10:5D:EF:44:D2  vLinker MC-Android   ← use this one
C0:10:5D:EF:44:D2  vLinker MC-IOS
```

Take the **`-Android`** one. That's the Classic/BR-EDR interface carrying
the serial (SPP) profile that `rfcomm` needs. The `-IOS` entry is BLE;
pairing it appears to succeed and then never delivers any OBD data. Your
own addresses will differ — never reuse the ones above.

Then:

```bash
git clone https://github.com/ifmeidan/ex-30-driver-display.git
cd ex-30-driver-display/ex30-companion-runner
chmod +x scripts/*.sh
VLINKER_MAC=AA:BB:CC:DD:EE:FF ./scripts/setup_lite.sh
```

The script installs packages, applies boot/display tweaks for the
Waveshare panel, pairs and trusts the vLinker (PIN defaults to `1234`,
the vLinker factory default — override with `VLINKER_PIN=...`), creates
the `rfcomm-bind` service, builds the Python environment, and installs
`driver-display.service` so the dashboard starts on boot.

## 4. Set up the WiFi bridge (one-time)

The head unit and the Pi talk over the Pi's own WiFi access point — no
internet involved, and the OBD link stays on Bluetooth so the radio is
free:

```bash
AP_COUNTRY=<your-two-letter-code> AP_PASS=<your-passphrase> ./scripts/install_pi_ap.sh
```

Both variables are **required**, and neither has a default:

- `AP_COUNTRY` sets the WiFi regulatory domain so the radio complies with
  your local regulations. There is no correct value to ship.
- `AP_PASS` is your access point's passphrase (8+ characters). **Choose
  your own.** Anyone in WiFi range who joins this AP can reach the bridge
  port and feed the driver display fabricated speed and power values, so
  a passphrase published in a repository would be no protection at all.

Defaults you can override: SSID is **`ex30-pi`** (`AP_SSID=…`), and the
Pi's address on the AP is **`192.168.4.1`**.

> **Heads-up**: if you're SSH'd in over the Pi's WiFi, bringing the AP up
> drops your session (one radio). Run this step over Ethernet, or just
> reconnect afterwards by joining the new AP (the Pi is `192.168.4.1`).

Then install the clock-sync helper — the Pi has no battery-backed clock,
so the head unit's GPS time is used to set it on every drive:

```bash
./scripts/install_clock_sync.sh
sudo timedatectl set-timezone <your/timezone>
```

Reboot (`sudo reboot`). The display should come up on its own, show the
boot animation, and sit on the loading screen — it's waiting for the car
now. Useful checks:

```bash
systemctl status rfcomm-bind driver-display
journalctl -u driver-display -f
```

## 5. Install the display in the car

1. Mount the display behind the steering wheel (3D-printed mount or
   equivalent) so it does **not** obstruct your view.
2. Power the Pi + display from the step-down converter, wired to an
   **ignition-switched** fuse via an add-a-fuse tap — the Pi must power
   off with the car. Do not wire it to an always-on circuit.
3. Leave the vLinker in the OBD2 port.
4. Route the HDMI/USB cables behind the column trim.

## 6. Get the AAOS companion app

The **EX30 Companion** app installs through Google Play on the car (AAOS
does not allow sideloading). The current release is **v1.0.1**, in
**closed testing**:

> **To join**: open an issue on this repository (or contact
> [@ifmeidan](https://github.com/ifmeidan)) with the Google account
> email you use on the car's Google Play, and you'll be added to the
> testing track. Once Google Play's closed-testing conditions are met,
> the app will be publicly available to all users — no signup needed,
> and testers keep getting updates either way.

After you're enrolled: on the car's center screen, open **Google Play**,
search for **EX30 Companion**, install.

Until you have the app, the display runs **OBD2-only**: brake gauge,
power/regen, battery temperature and odometer work; speed shows `–` and
SoC/range stay empty (they are AAOS-sourced). In that mode run the
display with `--no-standby` (in `scripts/launch.sh`, change the last
line to `exec python main.py --no-standby`) — standby's wake-up signal
comes from the app, so without it the screen blanks at long stops and
only returns after a power cycle.

## 7. Configure the app (one-time, while parked)

1. **Join the Pi's WiFi**: car Settings → Network → WiFi → connect to
   **`ex30-pi`** with the passphrase you set during AP install. The network has no
   internet — if the head unit asks, choose **"Stay connected"** /
   "Keep this network". The car keeps using its own cellular data for
   everything else; the app pins only its own socket to this WiFi.
2. Open **EX30 Companion**, enter the Pi host: **`192.168.4.1`**, tap
   **Connect**.
3. **Grant the car permissions** when prompted (a one-time consolidated
   prompt): vehicle **speed** and **energy** are "dangerous"-tier on
   AAOS and need explicit approval; the rest are granted at install.
   Also allow **notifications** — the bridge runs as a foreground
   service and AAOS requires its status notification.
4. **Keep the app alive in the background** — this matters. In car
   Settings → Apps → EX30 Companion:
   - **Notifications**: allowed (the foreground service depends on it),
   - **Battery / App power management** (naming varies by head-unit
     software version): set to **Unrestricted / Not optimized**, so the
     system never puts the bridge service to sleep mid-drive.
   The app auto-starts on head-unit boot and reconnects on its own; you
   should never need to open it again after this screen.

## 8. First drive checklist

- Power on the car → Pi boots → iron-mark splash → loading screen shows
  `VEHICLE DATA` (OBD) and `CABIN LINK` (AAOS) going green → dashboard.
- Speed on the display matches the car's own speedometer exactly (it is
  literally the same calibrated value).
- Plug in a charge cable → the display flips to the charging screen.
- Lock the car and walk away → the app says goodbye, the Pi stops
  polling, the vLinker sleeps. See the power note in the
  [README](README.md#parked-car-power--sleep-protection); keep an eye on
  the 12 V behavior during your first days and stop using the setup if
  you ever observe abnormal drain.

## Troubleshooting

| Symptom | Check |
|---|---|
| `VEHICLE DATA` never connects | `sudo systemctl status rfcomm-bind`; re-pair with `bluetoothctl` (PIN `1234`); car must be awake |
| `CABIN LINK` never connects | Head unit joined `ex30-pi`? App host set to `192.168.4.1`? Battery optimization disabled for the app? |
| Head unit drops the `ex30-pi` WiFi | Re-select it and confirm "Stay connected" despite no internet |
| Display blank | `journalctl -u driver-display -f`; HDMI ribbon seated? `display_auto_detect=0` present in `/boot/firmware/config.txt`? cage needs seatd — `systemctl status seatd` |
| Clock wrong on the display | `./scripts/install_clock_sync.sh` run? Timezone set? It corrects within seconds of the first AAOS frame |
| Speed shows `–` | The AAOS bridge is stale (15 s without frames) — speed is AAOS-only by design (see the README source table). Check the AAOS dot bottom-left, the head unit's WiFi, and the app's battery-optimization setting |
| Values look stale | Only the power/regen gauge falls back to OBD2 when the bridge is stale; SoC/ambient hold their last value and range hides. Brake, battery temp and odometer are always OBD2 |

For deeper debugging, both sides log: `logs/` on the Pi (bridge frames
as JSONL, app log), and `adb logcat` on the head unit if you're enrolled
as a developer on your own car.
