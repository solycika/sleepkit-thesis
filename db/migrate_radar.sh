#!/bin/bash
# ═══════════════════════════════════════════════════════════
# SleepKit – Einmalige mmWave-Migration auf dem Pi
# ═══════════════════════════════════════════════════════════
# Aggregiert alle vorhandenen sensor_radar Rohdaten zu
# Minuten-Werten in sensor_radar_minute.
# Setzt sync-Status: Rohdaten synced=1 (kein Cloud-Sync mehr),
# Minuten-Aggregate synced=0 (kommen mit dem nächsten Sync raus).
# ═══════════════════════════════════════════════════════════

set -e

DB="/opt/sleepkit/data/sleepkit.db"
SCHEMA="/tmp/sensor_radar_minute_sqlite.sql"

if [[ ! -f "$DB" ]]; then
    echo "FEHLER: Datenbank nicht gefunden: $DB"
    exit 1
fi

if [[ ! -f "$SCHEMA" ]]; then
    echo "FEHLER: Schema-Datei nicht gefunden: $SCHEMA"
    echo "Bitte zuerst sensor_radar_minute_sqlite.sql nach /tmp/ kopieren."
    exit 1
fi

echo "═══════════════════════════════════════════════════════════"
echo "  SleepKit mmWave-Migration"
echo "═══════════════════════════════════════════════════════════"

# 1. Schema anwenden
echo "──▶ [1/4] Schema anwenden..."
sudo -u monika sqlite3 "$DB" < "$SCHEMA"
echo "    ✔ Tabelle sensor_radar_minute angelegt"

# 2. Ist-Stand vor Migration
echo "──▶ [2/4] Ist-Stand vor Migration..."
RAW_COUNT=$(sudo -u monika sqlite3 "$DB" "SELECT COUNT(*) FROM sensor_radar;")
RAW_UNSYNCED=$(sudo -u monika sqlite3 "$DB" "SELECT COUNT(*) FROM sensor_radar WHERE synced=0;")
echo "    Rohdaten gesamt:    $RAW_COUNT"
echo "    Rohdaten unsynced:  $RAW_UNSYNCED"

# 3. Aggregation: Minuten-Bucket pro session_id
echo "──▶ [3/4] Aggregiere Rohdaten zu Minuten-Werten..."
sudo -u monika sqlite3 "$DB" <<'SQL'
INSERT OR IGNORE INTO sensor_radar_minute
    (session_id, ts_minute, n_samples,
     presence_share, movement_share,
     distance_avg, distance_min, distance_max,
     synced)
SELECT
    session_id,
    substr(ts, 1, 16) || ':00' AS ts_minute,
    COUNT(*) AS n_samples,
    ROUND(AVG(CASE WHEN presence >= 1 THEN 1.0 ELSE 0.0 END), 3) AS presence_share,
    ROUND(AVG(CASE WHEN presence  = 2 THEN 1.0 ELSE 0.0 END), 3) AS movement_share,
    ROUND(AVG(distance_cm), 1) AS distance_avg,
    MIN(distance_cm) AS distance_min,
    MAX(distance_cm) AS distance_max,
    0 AS synced
FROM sensor_radar
GROUP BY session_id, substr(ts, 1, 16);
SQL
MIN_COUNT=$(sudo -u monika sqlite3 "$DB" "SELECT COUNT(*) FROM sensor_radar_minute;")
echo "    ✔ Minuten-Aggregate erzeugt: $MIN_COUNT"

# 4. sensor_radar als "synced" markieren — verhindert dass
#    der Sync-Service die 458k Rohwerte zur Cloud schickt.
echo "──▶ [4/4] Rohdaten als synced markieren (kein Cloud-Sync)..."
sudo -u monika sqlite3 "$DB" "UPDATE sensor_radar SET synced=1 WHERE synced=0;"
NEW_UNSYNCED=$(sudo -u monika sqlite3 "$DB" "SELECT COUNT(*) FROM sensor_radar WHERE synced=0;")
echo "    ✔ Rohdaten unsynced jetzt: $NEW_UNSYNCED (sollte 0 sein)"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Migration abgeschlossen"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Rohdaten bleiben lokal auf dem Pi (30 Tage Retention)."
echo "  Minuten-Aggregate werden mit dem nächsten Sync zur Cloud."
echo ""
echo "  Nächste Schritte:"
echo "    1. collector.py + sync_mqtt.py + Schema deployen"
echo "    2. systemctl restart sleepkit-collector sleepkit-mqtt-sync"
echo "    3. Bridge auf EC2 mit neuer Tabelle starten"
echo ""
