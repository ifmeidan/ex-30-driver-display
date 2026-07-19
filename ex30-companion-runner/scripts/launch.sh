#!/bin/bash
# Launch script for the EX30 Driver Display.
# Called by cage via systemd — cage provides the Wayland compositor.

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# Tell Qt to use Wayland (provided by cage)
export QT_QPA_PLATFORM=wayland

source venv/bin/activate
exec python main.py
