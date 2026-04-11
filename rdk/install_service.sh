#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run with sudo."
  exit 1
fi

WORKDIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
SERVICE_NAME="${SERVICE_NAME:-rdk-face-tracker}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
CONFIG_PATH="${CONFIG_PATH:-${WORKDIR}/tracker_config.json}"
FACE_TRACKER_PATH="${FACE_TRACKER_PATH:-${WORKDIR}/face_tracker.py}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_PATH="/var/log/${SERVICE_NAME}.log"

if [[ ! -f "${FACE_TRACKER_PATH}" ]]; then
  echo "face_tracker.py not found: ${FACE_TRACKER_PATH}"
  exit 1
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "tracker config not found: ${CONFIG_PATH}"
  exit 1
fi

cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=RDK Face Tracker
After=local-fs.target systemd-udev-settle.service
Wants=systemd-udev-settle.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=${WORKDIR}
ExecStartPre=/bin/sh -c 'for i in \$(seq 1 30); do ls /dev/video* >/dev/null 2>&1 && exit 0; sleep 1; done; echo "camera device not ready" >&2; exit 1'
ExecStart=${PYTHON_BIN} ${FACE_TRACKER_PATH} --config ${CONFIG_PATH}
Restart=always
RestartSec=5
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
