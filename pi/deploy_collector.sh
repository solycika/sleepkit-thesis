#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# SleepKit Collector + MQTT Sync – Deployment-Skript
# Kopiert Dateien an die richtigen Stellen und aktiviert
# die systemd-Services (Collector + MQTT Sync).
#
# Voraussetzung: setup_sleepkit.sh wurde bereits ausgeführt!
# Ausführen: sudo bash deploy_collector.sh
# ═══════════════════════════════════════════════════════════

set -euo pipefail

SK_DIR="/opt/sleepkit"
SK_USER="monika"

echo "═══ SleepKit Collector + MQTT Sync Deployment ═══"
echo ""

# ── 1. Dateien kopieren ──────────────────────────────────
echo "──▶ [1/5] Dateien kopieren..."

# Schema
cp sleepkit_schema.sql "${SK_DIR}/src/sleepkit_schema.sql"
echo "  ✔ sleepkit_schema.sql → ${SK_DIR}/src/"

# Collector
cp collector.py "${SK_DIR}/src/collector.py"
chmod +x "${SK_DIR}/src/collector.py"
echo "  ✔ collector.py → ${SK_DIR}/src/"

# MQTT Sync Service
cp sync_mqtt.py "${SK_DIR}/src/sync_mqtt.py"
chmod +x "${SK_DIR}/src/sync_mqtt.py"
echo "  ✔ sync_mqtt.py → ${SK_DIR}/src/"

# Rechte setzen
chown -R "${SK_USER}:${SK_USER}" "${SK_DIR}"

# ── 2. MQTT-Konfiguration prüfen ────────────────────────
echo "──▶ [2/5] MQTT-Konfiguration prüfen..."
MQTT_CFG="${SK_DIR}/config/mqtt.yaml"
if [ ! -f "${MQTT_CFG}" ]; then
  echo "  ⚠ mqtt.yaml nicht gefunden – erstelle Template..."
  cat > "${MQTT_CFG}" << 'MQTTCFG'
# ═══════════════════════════════════════════════════════════
# SleepKit MQTT-Konfiguration
# Vor dem Start: broker_host, username, password setzen!
# ═══════════════════════════════════════════════════════════
mqtt:
  broker_host: "sleepkit.xyz"       # Domain oder IP der EC2-Instanz
  broker_port: 8883                 # 8883 = MQTT über TLS
  use_tls: true                     # TLS aktivieren (empfohlen)
  username: "sleepkit-pi"           # MQTT-Username (in Mosquitto angelegt)
  password: "HIER_PASSWORT_SETZEN"  # MQTT-Passwort
  topic_prefix: "sleepkit"          # Topic-Prefix (sleepkit/data/...)
  batch_size: 200                   # Max. Zeilen pro MQTT-Nachricht
  sync_interval_sec: 30             # Sekunden zwischen Sync-Runden
  qos: 1                            # QoS 1 = mindestens einmal zugestellt
  # ca_cert: "/opt/sleepkit/config/ca.crt"  # Optional: eigenes CA-Zertifikat
MQTTCFG
  chown "${SK_USER}:${SK_USER}" "${MQTT_CFG}"
  chmod 600 "${MQTT_CFG}"  # Nur Owner darf lesen (Passwort!)
  echo "  ✔ Template erstellt: ${MQTT_CFG}"
  echo "  ⚠ WICHTIG: broker_host und password in mqtt.yaml anpassen!"
else
  echo "  ✔ mqtt.yaml vorhanden"
fi

# ── 3. Datenbank initialisieren ──────────────────────────
echo "──▶ [3/5] Datenbank initialisieren..."
sudo -u "${SK_USER}" sqlite3 "${SK_DIR}/data/sleepkit.db" < "${SK_DIR}/src/sleepkit_schema.sql"
echo "  ✔ SQLite-Datenbank erstellt: ${SK_DIR}/data/sleepkit.db"

# ── 4. systemd-Services installieren ─────────────────────
echo "──▶ [4/5] systemd-Services installieren..."

# Collector-Service
cp sleepkit-collector.service /etc/systemd/system/sleepkit-collector.service
echo "  ✔ sleepkit-collector.service installiert"

# MQTT Sync Service
cp sleepkit-mqtt-sync.service /etc/systemd/system/sleepkit-mqtt-sync.service
echo "  ✔ sleepkit-mqtt-sync.service installiert"

systemctl daemon-reload
systemctl enable sleepkit-collector.service
systemctl enable sleepkit-mqtt-sync.service
echo "  ✔ Services aktiviert (starten beim Boot)"

# ── 5. Prüfung ──────────────────────────────────────────
echo "──▶ [5/5] Prüfung..."

echo ""
echo "  Dateien:"
ls -la "${SK_DIR}/src/collector.py"
ls -la "${SK_DIR}/src/sync_mqtt.py"
ls -la "${SK_DIR}/src/sleepkit_schema.sql"
ls -la "${SK_DIR}/data/sleepkit.db"
ls -la "${SK_DIR}/config/mqtt.yaml"

echo ""
echo "  Datenbank-Tabellen:"
sqlite3 "${SK_DIR}/data/sleepkit.db" ".tables"

echo ""
echo "  Service-Status:"
systemctl status sleepkit-collector.service --no-pager || true
echo ""
systemctl status sleepkit-mqtt-sync.service --no-pager || true

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✔ Deployment abgeschlossen!"
echo ""
echo "  Befehle:"
echo "    sudo systemctl start sleepkit-collector    # Collector starten"
echo "    sudo systemctl start sleepkit-mqtt-sync    # MQTT Sync starten"
echo "    sudo systemctl stop sleepkit-collector     # Collector stoppen"
echo "    sudo systemctl stop sleepkit-mqtt-sync     # MQTT Sync stoppen"
echo "    journalctl -u sleepkit-collector -f        # Collector-Log"
echo "    journalctl -u sleepkit-mqtt-sync -f        # Sync-Log"
echo ""
echo "  Vor dem Start:"
echo "    1. User setzen:  /opt/sleepkit/src/set_user.sh user01 'Name'"
echo "    2. MQTT prüfen:  cat /opt/sleepkit/config/mqtt.yaml"
echo "    3. Collector:    sudo systemctl start sleepkit-collector"
echo "    4. Sync:         sudo systemctl start sleepkit-mqtt-sync"
echo ""
echo "  Daten prüfen:"
echo "    sqlite3 /opt/sleepkit/data/sleepkit.db 'SELECT * FROM sessions;'"
echo "    sqlite3 /opt/sleepkit/data/sleepkit.db 'SELECT count(*) FROM sync_log;'"
echo "═══════════════════════════════════════════════════════"
