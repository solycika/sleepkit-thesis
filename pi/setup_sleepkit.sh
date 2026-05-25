#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# SleepKit Raspberry Pi 5 – Komplettes Setup-Skript
# Ausführen als root: sudo bash /opt/sleepkit/src/setup_sleepkit.sh
# ═══════════════════════════════════════════════════════════

set -euo pipefail
LOGFILE="/opt/sleepkit/setup.log"
mkdir -p /opt/sleepkit
exec > >(tee -a "$LOGFILE") 2>&1
echo "═══ SleepKit Setup gestartet: $(date) ═══"

# ── 0. Variablen ──────────────────────────────────────
SK_USER="monika"
SK_DIR="/opt/sleepkit"
SK_SRC="${SK_DIR}/src"
SK_CFG="${SK_DIR}/config"
SK_DATA="${SK_DIR}/data"
SK_VENV="${SK_DIR}/venv"

# ── 1. System-Update ─────────────────────────────────
echo "──▶ [1/10] System-Update..."
apt update && apt -y full-upgrade

# ── 2. Zeitzone & Locale ─────────────────────────────
echo "──▶ [2/10] Zeitzone & Locale..."
timedatectl set-timezone Europe/Vienna
localectl set-locale LANG=de_AT.UTF-8

# ── 3. Systempakete ──────────────────────────────────
echo "──▶ [3/10] Systempakete installieren..."
apt -y install \
  git python3-venv python3-pip python3-yaml python3-dev \
  i2c-tools libgpiod2 python3-libgpiod \
  sqlite3 jq \
  libportaudio2 portaudio19-dev \
  alsa-utils \
  curl wget

# ── 4. Benutzerrechte ────────────────────────────────
echo "──▶ [4/10] Benutzerrechte setzen..."
usermod -aG i2c,spi,gpio,dialout,audio "${SK_USER}"

# ── 5. Verzeichnisstruktur ───────────────────────────
echo "──▶ [5/10] Verzeichnisse anlegen..."
mkdir -p "${SK_SRC}" "${SK_CFG}" "${SK_DATA}" "${SK_DIR}/logs"

# active_user.json – wird vor jeder Messung gesetzt
cat > "${SK_CFG}/active_user.json" << 'USRCFG'
{
  "user_id": "user01",
  "user_name": "Testperson 1",
  "measurement_start": null,
  "measurement_end": null
}
USRCFG

# sensors.yaml – Sensor-Konfiguration
cat > "${SK_CFG}/sensors.yaml" << 'SENSCFG'
sensors:
  bme688:
    enabled: true
    bus: i2c
    address: 0x76
    interval_sec: 60
    # Temperatur, Feuchte, Druck, Gasqualität
  bh1750:
    enabled: true
    bus: i2c
    address: 0x23
    interval_sec: 30
    # Lichtstärke in Lux
  mhz19c:
    enabled: true
    bus: uart
    device: /dev/serial0
    baudrate: 9600
    interval_sec: 120
    warmup_sec: 180
    # CO2 in ppm
  pms5003:
    enabled: true
    bus: uart
    device: /dev/ttyAMA2
    baudrate: 9600
    interval_sec: 120
    # PM1.0, PM2.5, PM10 in µg/m³
  mmwave:
    enabled: true
    bus: uart
    device: /dev/ttyAMA3
    baudrate: 115200
    interval_sec: 0.5
    # Präsenz (0=niemand, 1=statisch, 2=Bewegung), Distanz
  inmp441:
    enabled: true
    bus: i2s
    channels: 2
    sample_rate: 16000
    interval_sec: 30
    analysis_window_sec: 5
    # RMS-Pegel, Peak, Schnarch-Score
SENSCFG

# mqtt.yaml – MQTT-Konfiguration (Template)
cat > "${SK_CFG}/mqtt.yaml" << 'MQTTCFG'
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
MQTTCFG
chmod 600 "${SK_CFG}/mqtt.yaml"     # Nur Owner lesen (Passwort!)

# ── 6. /boot/firmware/config.txt ─────────────────────
echo "──▶ [6/10] Hardware-Overlays konfigurieren..."
CONFIG="/boot/firmware/config.txt"
MARKER="# ═══ SleepKit HW ═══"

# Entferne alten SleepKit-Block falls vorhanden
if grep -q "${MARKER}" "${CONFIG}"; then
  sed -i "/${MARKER}/,/# ═══ SleepKit END ═══/d" "${CONFIG}"
fi

cat >> "${CONFIG}" << 'HWCFG'

# ═══ SleepKit HW ═══
# I2C für BME688, BH1750, DS3231
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=100000

# SPI (für zukünftige Erweiterungen)
dtparam=spi=on

# UART0 für MH-Z19C CO2-Sensor (GPIO14=TX, GPIO15=RX)
enable_uart=1

# RTC DS3231 auf I2C
dtoverlay=i2c-rtc,ds3231

# UART2 für PMS5003 Feinstaub (GPIO0=TX, GPIO1=RX)
dtoverlay=uart2

# UART3 für mmWave Radar (GPIO4=TX, GPIO5=RX)
dtoverlay=uart3

# I2S MEMS-Mikrofone (INMP441 x2)
# Falls i2s-mems-mic nicht verfügbar, googlevoicehat-soundcard verwenden
dtoverlay=googlevoicehat-soundcard
# ═══ SleepKit END ═══
HWCFG

# ── 7. Fake-HW-Clock entfernen, NTP aktivieren ──────
echo "──▶ [7/10] RTC & NTP konfigurieren..."
apt -y remove --purge fake-hwclock 2>/dev/null || true
systemctl disable --now fake-hwclock 2>/dev/null || true
systemctl enable --now systemd-timesyncd

# ── 8. Python Virtual Environment ───────────────────
echo "──▶ [8/10] Python venv & Pakete..."
python3 -m venv "${SK_VENV}"
source "${SK_VENV}/bin/activate"

pip install --upgrade pip
pip install \
  bme680 \
  smbus2 \
  mh-z19 \
  pyserial \
  sounddevice \
  numpy \
  scipy \
  pyyaml \
  requests \
  boto3 \
  paho-mqtt

deactivate

# ── 9. WLAN-Skript (NetworkManager + wpa_supplicant Fallback) ──
echo "──▶ [9/10] WLAN-Verwaltungsskript..."
cat > "${SK_SRC}/wifi_add.sh" << 'WIFISCRIPT'
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
# WLAN hinzufügen (NetworkManager – Bookworm/Pi5)
# Verwendung:
#   sudo /opt/sleepkit/src/wifi_add.sh "SSID" "PASSWORT"
#   sudo /opt/sleepkit/src/wifi_add.sh "SSID" "PASSWORT" 10  # Priorität
# ─────────────────────────────────────────────────────

set -euo pipefail

SSID="${1:?Fehlt: SSID als 1. Argument}"
PASS="${2:?Fehlt: Passwort als 2. Argument}"
PRIO="${3:-}"

# Prüfe ob NetworkManager oder wpa_supplicant
if command -v nmcli &>/dev/null && systemctl is-active --quiet NetworkManager; then
  echo "Verwende NetworkManager..."
  # Vorhandene Verbindung entfernen falls existent
  nmcli con delete "${SSID}" 2>/dev/null || true
  nmcli dev wifi connect "${SSID}" password "${PASS}"
  if [ -n "${PRIO}" ]; then
    nmcli con modify "${SSID}" connection.autoconnect-priority "${PRIO}"
  fi
  echo "✔ WLAN '${SSID}' verbunden (NetworkManager)."

elif command -v wpa_cli &>/dev/null; then
  echo "Verwende wpa_supplicant..."
  IFACE="${IFACE:-wlan0}"
  idx=$(wpa_cli -i "$IFACE" add_network | tail -n1)
  wpa_cli -i "$IFACE" set_network "$idx" ssid "\"${SSID}\""
  wpa_cli -i "$IFACE" set_network "$idx" psk "\"${PASS}\""
  wpa_cli -i "$IFACE" set_network "$idx" key_mgmt WPA-PSK >/dev/null
  [ -n "${PRIO}" ] && wpa_cli -i "$IFACE" set_network "$idx" priority "${PRIO}" >/dev/null
  wpa_cli -i "$IFACE" enable_network "$idx" >/dev/null
  wpa_cli -i "$IFACE" save_config >/dev/null
  wpa_cli -i "$IFACE" reconfigure >/dev/null
  echo "✔ WLAN '${SSID}' verbunden (wpa_supplicant)."

else
  echo "✘ FEHLER: Weder NetworkManager noch wpa_supplicant gefunden!"
  exit 1
fi
WIFISCRIPT

chmod +x "${SK_SRC}/wifi_add.sh"

# ── 10. User-Auswahl-Skript ─────────────────────────
echo "──▶ [10/10] User-Auswahl-Skript..."
cat > "${SK_SRC}/set_user.sh" << 'USERSCRIPT'
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
# Aktiven Benutzer für Messung setzen
#   /opt/sleepkit/src/set_user.sh user01 "Max Mustermann"
#   /opt/sleepkit/src/set_user.sh user02 "Erika Muster"
# ─────────────────────────────────────────────────────

CFG="/opt/sleepkit/config/active_user.json"
UID_ARG="${1:?Fehlt: user_id (z.B. user01, user02, user03, user04)}"
NAME_ARG="${2:?Fehlt: user_name (z.B. 'Max Mustermann')}"
NOW=$(date -Iseconds)

cat > "${CFG}" << EOF
{
  "user_id": "${UID_ARG}",
  "user_name": "${NAME_ARG}",
  "measurement_start": "${NOW}",
  "measurement_end": null
}
EOF

echo "✔ Aktiver User: ${NAME_ARG} (${UID_ARG}) ab ${NOW}"
echo "  Konfiguration: ${CFG}"
USERSCRIPT

chmod +x "${SK_SRC}/set_user.sh"

# ── Abschluss ───────────────────────────────────────
chown -R "${SK_USER}:${SK_USER}" "${SK_DIR}"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✔ SleepKit Setup abgeschlossen!"
echo "  ⚠ Bitte REBOOT durchführen: sudo reboot"
echo ""
echo "  Nach Reboot prüfen:"
echo "    i2cdetect -y 1          # I2C-Geräte (0x23, 0x68, 0x76)"
echo "    ls /dev/ttyAMA*          # UART-Ports (AMA0, AMA2, AMA3)"
echo "    arecord -l               # Audio/I2S-Geräte"
echo "    cat /opt/sleepkit/config/active_user.json"
echo "    cat /opt/sleepkit/config/sensors.yaml"
echo "    cat /opt/sleepkit/config/mqtt.yaml"
echo ""
echo "  User für Messung setzen:"
echo "    /opt/sleepkit/src/set_user.sh user01 'Name'"
echo ""
echo "  MQTT-Konfiguration anpassen:"
echo "    nano /opt/sleepkit/config/mqtt.yaml"
echo "═══════════════════════════════════════════════════"
