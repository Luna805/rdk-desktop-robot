#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run with sudo."
  exit 1
fi

WORKDIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
SERVICE_NAME="${SERVICE_NAME:-rdk-ir-wake}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
CONFIG_PATH="${CONFIG_PATH:-${WORKDIR}/ir_wake_config.json}"
SCRIPT_PATH="${SCRIPT_PATH:-${WORKDIR}/ir_wake_bridge.py}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_PATH="/var/log/${SERVICE_NAME}.log"

if [[ ! -f "${SCRIPT_PATH}" ]]; then
  echo "ir_wake_bridge.py not found: ${SCRIPT_PATH}"
  exit 1
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "ir_wake_config.json not found: ${CONFIG_PATH}"
  exit 1
fi

cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=RDK IR Wake Bridge
After=local-fs.target systemd-udev-settle.service
Wants=systemd-udev-settle.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=${WORKDIR}
ExecStartPre=/bin/sh -c 'for i in \$(seq 1 30); do ls /dev/ttyACM* /dev/ttyUSB* >/dev/null 2>&1 && exit 0; sleep 1; done; echo "ESP serial device not ready" >&2; exit 1'
ExecStart=${PYTHON_BIN} ${SCRIPT_PATH} --config ${CONFIG_PATH}
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:${LOG_PATH}
StandardError=append:${LOG_PATH}

[Install]
WantedBy=multi-user.target
EOF

touch "${LOG_PATH}"
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

echo "Installed ${SERVICE_NAME}."
echo "Service file: ${SERVICE_PATH}"
echo "Log file: ${LOG_PATH}"
echo "Check status with: sudo systemctl status ${SERVICE_NAME}"
