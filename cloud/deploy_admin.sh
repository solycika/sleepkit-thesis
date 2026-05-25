#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# SleepKit Admin-Endpoints Deploy (Patch v3)
# Hängt Admin-Endpoints an die bestehende main.py an.
#
# Ausführen auf der EC2:
#   sudo bash deploy_admin.sh
# ═══════════════════════════════════════════════════════════
set -euo pipefail

API_DIR="/opt/sleepkit-api"
API_USER="sleepkit"
SRC_FILE="${API_DIR}/src/main.py"
PATCH_FILE="/tmp/main_patch_v3.py"

if [[ $EUID -ne 0 ]]; then
  echo "✘ Bitte mit sudo ausführen"
  exit 1
fi

if [[ ! -f "${PATCH_FILE}" ]]; then
  echo "✘ Patch-Datei fehlt: ${PATCH_FILE}"
  echo "  Bitte zuerst hochladen:"
  echo "    cat main_patch_v3.py | ssh sleepkit \"cat > /tmp/main_patch_v3.py\""
  exit 1
fi

echo "═══ SleepKit Admin-Endpoints Patch v3.0 ═══"
echo ""

# 1. Backup
echo "──▶ [1/2] Backup + Patch anhängen..."
BACKUP="${SRC_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
cp "${SRC_FILE}" "${BACKUP}"
echo "  ✔ Backup: ${BACKUP}"

# Idempotenz: prüfe ob schon drin
if grep -q "list_users_detailed" "${SRC_FILE}"; then
  echo "  ! Admin-Endpoints scheinen bereits in ${SRC_FILE} zu sein."
  echo "    Überspringe Anhängen."
else
  cat "${PATCH_FILE}" >> "${SRC_FILE}"
  chown "${API_USER}:${API_USER}" "${SRC_FILE}"
  echo "  ✔ Patch an ${SRC_FILE} angehängt"
fi

# 2. Restart
echo "──▶ [2/2] FastAPI neustarten..."
systemctl restart sleepkit-api
sleep 2
if systemctl is-active --quiet sleepkit-api; then
  echo "  ✔ sleepkit-api läuft"
else
  echo "  ✘ sleepkit-api läuft NICHT"
  echo "    journalctl -u sleepkit-api -n 50 --no-pager"
  echo ""
  echo "  Rollback:"
  echo "    sudo cp ${BACKUP} ${SRC_FILE}"
  echo "    sudo systemctl restart sleepkit-api"
  exit 1
fi

echo ""
echo "═════════════════════════════════════════════════════════"
echo "  ✔ Admin-Endpoints deployed"
echo "═════════════════════════════════════════════════════════"
echo ""
echo "  Tests (sollten 401 zurückgeben = Auth fehlt → Endpoint live):"
echo "    curl -s -o /dev/null -w '%{http_code}\\n' \\"
echo "         https://DEINE-DOMAIN.example/api/admin/users/detailed"
echo ""
echo "  Im Browser als admin01 einloggen → Admin-Sicht erscheint."
echo ""
