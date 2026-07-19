#!/bin/bash
# Install the AAOS-bridge clock-sync helper + sudoers fragment.
# Run on the Pi (one-shot): bash scripts/install_clock_sync.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Strip any CRLF line endings the files may have picked up if they were
# edited on a Windows machine (a real gotcha with launch.sh in the past).
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
sed 's/\r$//' "$SCRIPT_DIR/aaos-set-time" > "$TMP"
sudo install -m 755 -o root -g root "$TMP" /usr/local/sbin/aaos-set-time

# Fill in the installing user for the sudoers rule.
sed -e 's/\r$//' -e "s/@PI_USER@/$(id -un)/g" "$SCRIPT_DIR/aaos-set-time.sudoers" > "$TMP"
sudo install -m 440 -o root -g root "$TMP" /etc/sudoers.d/aaos-set-time

# Validate the sudoers file — visudo -c refuses to leave a broken policy in
# place. If this fails, abort before the user is locked out of sudo.
if ! sudo visudo -c -f /etc/sudoers.d/aaos-set-time; then
    echo "sudoers validation failed; removing /etc/sudoers.d/aaos-set-time" >&2
    sudo rm -f /etc/sudoers.d/aaos-set-time
    exit 1
fi

echo "Installed."
echo "Self-test (should print nothing on success):"
sudo -n /usr/local/sbin/aaos-set-time "$(date +%s)"
echo "Self-test passed. Restart driver-display.service to pick up the helper."
