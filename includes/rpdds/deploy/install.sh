#!/bin/bash
# Red Pitaya DDS AXI DMA installer (monolithic).
# Runs on-board. Source dir = the dds_axi_dma/ parent of deploy/.
# Assumes /opt/redpitaya is the vfat boot partition; does remount,rw.
# vfat is fragile (no atomic rename across dirs), so we avoid mktemp+mv
# dances and sync between write groups.
# Also avoids restarting redpitaya_nginx since on this board the restart
# triggers a disruptive system flow.
#
# WARNING (observed 2026-04-23): running this script end-to-end in a
# single plink/SSH session hard-rebooted the board mid-install on the
# currently flashed Red Pitaya OS image (dmesg showed ext4 recovery on
# next boot, so it was NOT a clean shutdown). Root cause unidentified;
# most likely some Red Pitaya daemon reacting to the cp/systemd
# sequence. If this bites you, use deploy/install_split.sh.example
# instead - it runs the same operations as three separate short plink
# calls (files / systemd / fpga) and each phase is idempotent.

set -euo pipefail

APP_NAME="${APP_NAME:-dds_axi_dma_workbench}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_ROOT="/opt/redpitaya/www/apps/${APP_NAME}"
BACKEND_ROOT="${WEB_ROOT}/backend"
MODEL="$("/opt/redpitaya/bin/monitor" -f)"
PROJECT_ROOT="/opt/redpitaya/fpga/${MODEL}/${APP_NAME}"
SYSTEMD_UNIT="/etc/systemd/system/rp-dds-axi-dma.service"
# Legacy path; we removed the boot-time overlay-loader unit after it
# correlated with the board failing to come back up from `systemctl reboot`
# twice in a row. Kept here only so the installer can clean it up.
LEGACY_OVERLAY_UNIT="/etc/systemd/system/rp-dds-axi-dma-overlay.service"

# Remount /opt/redpitaya rw. Prefer the interactive-shell 'rw' helper,
# otherwise issue the remount directly. Non-interactive shells don't see
# the 'rw' alias, so always have the mount fallback.
remount_opt_rw() {
  if command -v rw >/dev/null 2>&1; then
    rw || true
    return 0
  fi
  if mountpoint -q /opt/redpitaya; then
    mount -o remount,rw /opt/redpitaya || {
      echo "ERROR: cannot remount /opt/redpitaya read-write" >&2
      exit 1
    }
  fi
}

remount_opt_rw

echo "[1/8] Preparing target folders..."
mkdir -p "${WEB_ROOT}" "${BACKEND_ROOT}" "${PROJECT_ROOT}"
sync

echo "[2/8] Copying frontend assets..."
cp -f "${SRC_DIR}/web/index.html"            "${WEB_ROOT}/index.html"
cp -f "${SRC_DIR}/web/app.js"                "${WEB_ROOT}/app.js"
cp -f "${SRC_DIR}/web/glow-tube-display.js"  "${WEB_ROOT}/glow-tube-display.js"

cat > "${WEB_ROOT}/nginx.conf" <<EOF
location /${APP_NAME}/api/ {
    rewrite ^/${APP_NAME}/api/(.*)$ /api/\$1 break;
    proxy_pass http://127.0.0.1:18888;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    add_header Cache-Control "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0";
}

location = /${APP_NAME} {
    return 302 /${APP_NAME}/index.html;
}
EOF
sync

echo "[3/8] Copying backend files..."
# Validate source *first* so a bad .py never reaches the target FS.
python3 -m py_compile "${SRC_DIR}"/ps/*.py

# Clean out old files from the backend dir but keep the dir entry itself;
# avoid any mv-across-dirs on vfat (which previously caused directory
# corruption and the kernel auto-remounting ro via errors=remount-ro).
mkdir -p "${BACKEND_ROOT}"
find "${BACKEND_ROOT}" -maxdepth 1 -type f \( -name '*.py' -o -name '*.pyc' \) -delete 2>/dev/null || true
rm -rf "${BACKEND_ROOT}/__pycache__" 2>/dev/null || true
# Also remove any leftover staging dirs from prior (buggy) installer runs.
find "${WEB_ROOT}" -maxdepth 1 -type d -name 'backend.staging.*' -exec rm -rf {} + 2>/dev/null || true
rm -rf "${WEB_ROOT}/backend.prev" 2>/dev/null || true
sync

cp -f "${SRC_DIR}"/ps/*.py "${BACKEND_ROOT}/"
find "${BACKEND_ROOT}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
sync

echo "[4/8] Installing helper files..."
cp -f "${SRC_DIR}/deploy/dds_dma_reserved_mem.dts.template" "${PROJECT_ROOT}/dds_dma_reserved_mem.dts.template"
cp -f "${SRC_DIR}/deploy/load_overlay.sh.example"           "${PROJECT_ROOT}/load_overlay.sh"
chmod +x "${PROJECT_ROOT}/load_overlay.sh"
cp -f "${SRC_DIR}/deploy/verify.sh.example"                 "${PROJECT_ROOT}/verify.sh"
chmod +x "${PROJECT_ROOT}/verify.sh"
sync

echo "[5/8] Installing systemd units..."
# Clean up any legacy boot-persistence overlay unit from earlier installs.
# We no longer ship it: loading the DDS bitstream at boot via systemd
# correlated with the board failing to boot afterwards (twice). Users
# load DDS on demand instead - see load_overlay.sh.
if [[ -f "${LEGACY_OVERLAY_UNIT}" ]] \
   || systemctl list-unit-files --no-legend 2>/dev/null | grep -q '^rp-dds-axi-dma-overlay\.service'; then
  echo "  - Removing legacy rp-dds-axi-dma-overlay.service"
  systemctl stop rp-dds-axi-dma-overlay.service 2>/dev/null || true
  systemctl disable rp-dds-axi-dma-overlay.service 2>/dev/null || true
  rm -f "${LEGACY_OVERLAY_UNIT}"
  systemctl daemon-reload
fi

cat > "${SYSTEMD_UNIT}" <<EOF
[Unit]
Description=Red Pitaya DDS AXI DMA backend service
After=network.target
Wants=network.target

[Service]
Type=simple
WorkingDirectory=${BACKEND_ROOT}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=${BACKEND_ROOT}
ExecStart=/usr/bin/env python3 ${BACKEND_ROOT}/dds_service.py
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable rp-dds-axi-dma.service

echo "[6/8] Installing FPGA overlay files if present..."
BIT_OK=0
DTBO_OK=0

if [[ -f "${SRC_DIR}/build/fpga.bit.bin" ]]; then
  cp -f "${SRC_DIR}/build/fpga.bit.bin" "${PROJECT_ROOT}/fpga.bit.bin"
  BIT_OK=1
  echo "  - Copied build/fpga.bit.bin"
else
  echo "  - build/fpga.bit.bin not found"
fi

if [[ -f "${SRC_DIR}/build/fpga.dtbo" ]]; then
  cp -f "${SRC_DIR}/build/fpga.dtbo" "${PROJECT_ROOT}/fpga.dtbo"
  DTBO_OK=1
  echo "  - Copied build/fpga.dtbo"
else
  echo "  - build/fpga.dtbo not found"
fi
sync

echo "[7/8] Skipping nginx restart (Red Pitaya's redpitaya_nginx restart is disruptive)."
echo "      If the /${APP_NAME}/ route does not respond, reload manually:"
echo "        systemctl reload redpitaya_nginx"
echo "      or reboot the board once."

echo "[8/8] Loading overlay now (manual, not at boot)..."
if [[ "${BIT_OK}" -eq 1 && "${DTBO_OK}" -eq 1 ]]; then
  # Run load_overlay.sh inline so output is visible here. DDS is loaded
  # only for this session; after a reboot the user re-runs this manually:
  #   ssh root@rp ${PROJECT_ROOT}/load_overlay.sh
  "${PROJECT_ROOT}/load_overlay.sh"
  echo "Overlay loaded. Current project:"
  cat /tmp/loaded_fpga.inf 2>/dev/null || true
  # Start backend against the freshly programmed PL.
  systemctl restart rp-dds-axi-dma.service
else
  echo "Overlay not loaded because fpga.bit.bin and fpga.dtbo are both required."
  echo "Backend service is enabled; after programming the PL you can:"
  echo "  ${PROJECT_ROOT}/load_overlay.sh && systemctl restart rp-dds-axi-dma.service"
fi

echo
echo "NOTE: DDS is NOT loaded automatically at boot. After any reboot, run:"
echo "  ssh root@rp-f0d653.local ${PROJECT_ROOT}/load_overlay.sh"
echo "Optionally also restart the backend to refresh its mmap:"
echo "  ssh root@rp-f0d653.local systemctl restart rp-dds-axi-dma.service"

sync

echo
echo "Open in browser:"
echo "  http://$(hostname -I | awk '{print $1}')/${APP_NAME}/index.html"
