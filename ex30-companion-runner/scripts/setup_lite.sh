#!/bin/bash
# =============================================================================
# EX30 Driver Display — Pi OS Lite Bootstrap Script
# =============================================================================
# Run this once on a fresh Raspberry Pi OS Lite (64-bit) image.
#
# Prerequisites:
#   - Pi OS Lite flashed, SSH enabled, a user with sudo rights created
#   - Pi connected to network (Ethernet or pre-configured WiFi)
#   - This repository cloned onto the Pi; run the script from inside it
#
# Usage:
#   VLINKER_MAC=AA:BB:CC:DD:EE:FF ./scripts/setup_lite.sh
#
#   VLINKER_MAC  (required)  Bluetooth MAC of your OBD2 adapter
#                            (find it with: bluetoothctl → scan on)
#   VLINKER_PIN  (optional)  pairing PIN, default 1234 (vLinker factory default)
#   REBOOT       (optional)  set to 1 to reboot when this script finishes. Off by
#                            default — the AP and clock-sync installs run after.
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[SETUP]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PI_USER="$(id -un)"
VLINKER_MAC="${VLINKER_MAC:?Set VLINKER_MAC to the Bluetooth MAC of your OBD2 adapter, e.g. VLINKER_MAC=AA:BB:CC:DD:EE:FF ./scripts/setup_lite.sh}"
VLINKER_PIN="${VLINKER_PIN:-1234}"
RFCOMM_CHANNEL=1

log "Project dir: $PROJECT_DIR"
log "Service user: $PI_USER"
log "OBD adapter:  $VLINKER_MAC (PIN $VLINKER_PIN)"

# =============================================================================
# 1. System update & core packages
# =============================================================================
log "Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y

log "Installing core dependencies..."
sudo apt-get install -y \
    git \
    python3 \
    python3-venv \
    python3-pip \
    bluetooth \
    bluez \
    bluez-tools \
    rfkill \
    expect \
    cage \
    seatd \
    libgl1 \
    libegl1 \
    libgles2 \
    libxkbcommon0 \
    fonts-dejavu-core

# cage (the Wayland compositor hosting the dashboard) needs a seat manager;
# Pi OS Lite ships without one, so enable seatd now.
sudo systemctl enable --now seatd

# =============================================================================
# 2. Boot speed optimizations
# =============================================================================
log "Applying boot speed optimizations..."

# Disable unused services
DISABLE_SERVICES=(
    avahi-daemon          # mDNS — not needed
    triggerhappy          # hotkey daemon
    systemd-timesyncd     # NTP — no internet in car
    ModemManager          # modem support
    apt-daily.service
    apt-daily-upgrade.service
    apt-daily.timer
    apt-daily-upgrade.timer
    man-db.timer
    e2scrub_all.timer
)

for svc in "${DISABLE_SERVICES[@]}"; do
    if systemctl list-unit-files "$svc" &>/dev/null; then
        sudo systemctl disable "$svc" 2>/dev/null || true
        sudo systemctl mask "$svc" 2>/dev/null || true
        log "  Disabled: $svc"
    fi
done

# GPU memory split — minimal for headless compute, enough for display
if ! grep -q "^gpu_mem=" /boot/firmware/config.txt; then
    echo "gpu_mem=64" | sudo tee -a /boot/firmware/config.txt > /dev/null
    log "  Set gpu_mem=64"
fi

# Disable splash screen for faster boot
if ! grep -q "^disable_splash=1" /boot/firmware/config.txt; then
    echo "disable_splash=1" | sudo tee -a /boot/firmware/config.txt > /dev/null
fi

# Kernel boot params — quiet boot, no logo
CMDLINE="/boot/firmware/cmdline.txt"
if ! grep -q "quiet" "$CMDLINE"; then
    sudo sed -i 's/$/ quiet logo.nologo vt.global_cursor_default=0/' "$CMDLINE"
    log "  Added quiet boot params"
fi

# Disable display_auto_detect (needed for the Waveshare 8.8" panel)
if ! grep -q "^display_auto_detect=0" /boot/firmware/config.txt; then
    echo "display_auto_detect=0" | sudo tee -a /boot/firmware/config.txt > /dev/null
    log "  Disabled display_auto_detect"
fi

# =============================================================================
# 3. Bluetooth setup — pair & persist the OBD2 adapter
# =============================================================================
log "Configuring Bluetooth..."

# Unblock Bluetooth
sudo rfkill unblock bluetooth
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

# Wait for adapter
sleep 2
sudo hciconfig hci0 up || warn "hci0 may already be up"

# Pair the OBD2 adapter (non-interactive)
log "Pairing OBD2 adapter ($VLINKER_MAC)..."
# BlueZ only pairs devices already in the adapter's discovery cache, which is
# empty on a fresh flash — so the agent scans first, then pairs.
# Set up the PIN agent for legacy pairing
cat > /tmp/bt_pair.sh << BTEOF
#!/usr/bin/expect -f
set timeout 45
spawn bluetoothctl
expect "#"
send "power on\r"
expect "#"
send "agent on\r"
expect "#"
send "default-agent\r"
expect "#"
send "scan on\r"
sleep 12
send "scan off\r"
expect "#"
send "pair ${VLINKER_MAC}\r"
expect {
    -re "PIN code|Enter PIN" { send "${VLINKER_PIN}\r"; exp_continue }
    "Pairing successful" {}
    -re "Failed to pair|not available" {}
    timeout {}
}
expect "#"
send "trust ${VLINKER_MAC}\r"
expect "#"
send "quit\r"
expect eof
BTEOF

bt_is_paired() {
    bluetoothctl info "$VLINKER_MAC" 2>/dev/null | grep -q "Paired: yes"
}

# Re-running this script is normal, and BlueZ reports an existing bond as
# "Failed to pair: org.bluez.Error.AlreadyExists" — so the bond is checked
# directly rather than inferred from bluetoothctl's output.
if bt_is_paired; then
    log "  Adapter already paired — skipping"
elif command -v expect &>/dev/null; then
    chmod +x /tmp/bt_pair.sh
    /tmp/bt_pair.sh >/dev/null 2>&1 || true
    # Verify the bond itself; the expect script's exit code only reflects which
    # message it happened to match, not whether pairing actually took.
    if bt_is_paired; then
        log "  Adapter paired"
    else
        warn "Bluetooth pairing failed — pair manually:"
        warn "  bluetoothctl → power on → agent on → scan on → pair $VLINKER_MAC → PIN: $VLINKER_PIN → trust"
    fi
else
    warn "expect not installed — pair the adapter manually:"
    warn "  bluetoothctl → power on → agent on → scan on → pair $VLINKER_MAC → PIN: $VLINKER_PIN → trust → quit"
fi

# Trust is idempotent and required for rfcomm to reconnect unattended.
bluetoothctl trust "$VLINKER_MAC" >/dev/null 2>&1 || true

# =============================================================================
# 4. rfcomm bind persistence (systemd unit)
# =============================================================================
log "Creating rfcomm bind service..."

sudo tee /etc/systemd/system/rfcomm-bind.service > /dev/null << EOF
[Unit]
Description=Bind rfcomm0 to the OBD2 adapter
After=bluetooth.target
Requires=bluetooth.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/usr/sbin/rfkill unblock bluetooth
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/rfcomm bind 0 ${VLINKER_MAC} ${RFCOMM_CHANNEL}
ExecStop=/usr/bin/rfcomm release 0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable rfcomm-bind.service
log "  rfcomm-bind.service enabled"

# =============================================================================
# 5. Python environment
# =============================================================================
log "Setting up Python environment..."

cd "$PROJECT_DIR"

# Create venv and install dependencies
if [ ! -d "venv" ]; then
    python3 -m venv venv
    log "  Created virtual environment"
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
log "  Dependencies installed"

# Create logs directory
mkdir -p logs

# =============================================================================
# 6. Install driver display service
# =============================================================================
log "Installing driver display systemd service..."

# cage needs XDG_RUNTIME_DIR (/run/user/<uid>). logind only keeps that directory
# around while the user has a session, so without lingering the display starts
# fine while you are SSH'd in and fails on a real boot in the car.
sudo loginctl enable-linger "$PI_USER"

sed -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
    -e "s|@PI_USER@|$PI_USER|g" \
    -e "s|@PI_UID@|$(id -u "$PI_USER")|g" \
    "$PROJECT_DIR/scripts/driver-display.service" \
    | sudo tee /etc/systemd/system/driver-display.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable driver-display.service
log "  driver-display.service enabled"

# =============================================================================
# 7. Screen blanking — disable via cage/seatd
# =============================================================================
log "Disabling console blanking..."

# Prevent console blanking
if ! grep -q "consoleblank=0" "$CMDLINE"; then
    sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
fi

# =============================================================================
# Done
# =============================================================================
echo ""
log "=========================================="
log "  Setup complete!"
log "=========================================="
log ""
log "Next steps:"
log "  1. Set up the WiFi access point for the AAOS bridge:"
log "       AP_COUNTRY=<your-country-code> AP_PASS=<your-passphrase> ./scripts/install_pi_ap.sh"
log "  2. Install the clock-sync helper:"
log "       ./scripts/install_clock_sync.sh"
log "  3. Reboot to test autostart:"
log "       sudo reboot"
log "  4. The display app should launch automatically."
log ""
log "Troubleshooting:"
log "  - Check rfcomm:  sudo systemctl status rfcomm-bind"
log "  - Check display: sudo systemctl status driver-display"
log "  - Check logs:    journalctl -u driver-display -f"
log "  - Manual start:  cage -- $PROJECT_DIR/scripts/launch.sh"
log ""

# Opt-in only: the access point and clock-sync installs still have to run after
# this script, so rebooting here by default would cut the setup in half.
if [ "${REBOOT:-0}" = "1" ]; then
    log "REBOOT=1 — rebooting in 5 seconds (Ctrl-C to cancel)..."
    sleep 5
    sudo reboot
fi
