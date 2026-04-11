#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIN="${1:-33}"
STOP_US="${2:-1500}"

sudo python3 "$SCRIPT_DIR/servo_control.py" stop --pin "$PIN" --stop-us "$STOP_US" --disable
