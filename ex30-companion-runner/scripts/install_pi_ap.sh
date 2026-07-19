#!/bin/bash
# Install the EX30 Pi-as-AP NetworkManager profile.
# Run on the Pi (one-shot): bash scripts/install_pi_ap.sh
#
# Creates a WiFi access point on wlan0 at 192.168.4.1/24 with NetworkManager's
# built-in dnsmasq for DHCP. No internet upstream — the head unit joins this
# SSID purely so the AAOS companion app can reach the bridge receiver on
# port 7878.
#
# Usage: AP_COUNTRY=XX AP_PASS=… [AP_SSID=foo] bash scripts/install_pi_ap.sh
set -euo pipefail

AP_SSID="${AP_SSID:-ex30-pi}"
AP_IFACE="${AP_IFACE:-wlan0}"
AP_ADDR="${AP_ADDR:-192.168.4.1/24}"
AP_CON="${AP_CON:-ex30-pi-ap}"
# Required: the WiFi regulatory domain is per-country; there is no correct
# default to ship in a public repo.
AP_COUNTRY="${AP_COUNTRY:?Set AP_COUNTRY to your two-letter country code (e.g. AP_COUNTRY=DE) — it sets the WiFi regulatory domain}"
# Required, and deliberately without a default: any passphrase shipped here
# would be public, and anyone in WiFi range who joins the AP can reach the
# bridge port and feed the driver display fabricated values.
AP_PASS="${AP_PASS:?Set AP_PASS to your own WiFi passphrase (8+ characters) — no default is provided on purpose}"

if [ ${#AP_PASS} -lt 8 ]; then
    echo "AP_PASS must be at least 8 characters (WPA2 requirement)" >&2
    exit 1
fi

# Single radio: bringing the AP up replaces any WiFi client connection on
# $AP_IFACE. If you are SSH'd in over that WiFi, your session will drop —
# reconnect via Ethernet or by joining the new AP (${AP_ADDR%%/*}).
case "${SSH_CONNECTION:-}" in
    "") ;;
    *)  echo "WARNING: you appear to be connected over SSH. If that session"
        echo "         rides on $AP_IFACE it will drop when the AP comes up."
        ;;
esac

if ! command -v nmcli >/dev/null 2>&1; then
    echo "nmcli not found — this script requires NetworkManager (Pi OS Bookworm+)" >&2
    exit 1
fi

if ! ip link show "$AP_IFACE" >/dev/null 2>&1; then
    echo "Interface $AP_IFACE not present" >&2
    exit 1
fi

# Set the regulatory domain so the radio is allowed to TX on the AP channel.
# iw is the canonical way; raspi-config writes to /etc/default/crda on older
# images but Bookworm uses wireless-regdb directly.
if command -v iw >/dev/null 2>&1; then
    sudo iw reg set "$AP_COUNTRY" || true
fi
# Persist across reboots via wpa_supplicant.conf if present, and via
# /etc/default/crda for belt-and-braces.
if [ -f /etc/default/crda ]; then
    if grep -q '^REGDOMAIN=' /etc/default/crda; then
        sudo sed -i "s/^REGDOMAIN=.*/REGDOMAIN=$AP_COUNTRY/" /etc/default/crda
    else
        echo "REGDOMAIN=$AP_COUNTRY" | sudo tee -a /etc/default/crda >/dev/null
    fi
fi

# Make sure wpa_supplicant isn't masked from setup_lite.sh — NetworkManager
# uses it under the hood for AP-mode auth on most Pi images.
if systemctl is-enabled wpa_supplicant 2>/dev/null | grep -q masked; then
    sudo systemctl unmask wpa_supplicant
fi
sudo systemctl enable --now NetworkManager 2>/dev/null || true

# Idempotent: drop any prior version of this connection before recreating it,
# so re-running the installer always lands on a clean profile.
if nmcli -t -f NAME con show | grep -Fxq "$AP_CON"; then
    sudo nmcli con delete "$AP_CON"
fi

sudo nmcli con add \
    type wifi \
    ifname "$AP_IFACE" \
    con-name "$AP_CON" \
    autoconnect yes \
    ssid "$AP_SSID"

sudo nmcli con modify "$AP_CON" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel 6 \
    802-11-wireless.powersave 2 \
    ipv4.method shared \
    ipv4.addresses "$AP_ADDR" \
    ipv6.method ignore \
    connection.autoconnect-priority 100 \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.proto rsn \
    wifi-sec.pairwise ccmp \
    wifi-sec.group ccmp \
    wifi-sec.psk "$AP_PASS"

sudo nmcli con up "$AP_CON"

echo
echo "Installed NetworkManager AP profile '$AP_CON'."
echo "  SSID:       $AP_SSID"
echo "  Passphrase: $AP_PASS"
echo "  IP:         ${AP_ADDR%%/*}  (this Pi, on $AP_IFACE)"
echo "  Country:    $AP_COUNTRY"
echo
echo "Next steps:"
echo "  1. On the EX30 head unit, join SSID '$AP_SSID' (passphrase above)."
echo "     Android may say 'no internet' — that's expected; tap 'Stay connected'."
echo "  2. In the AAOS companion app, set bridge host to ${AP_ADDR%%/*}"
echo "     and tap Connect. The bridge socket binds to the WiFi network"
echo "     explicitly (see BridgeClient.kt), so this works even if"
echo "     the head unit's cellular is still its default route."
echo "  3. Confirm a frame landed (run from ex30-companion-runner/):"
echo "     tail logs/aaos_bridge_*.jsonl"
