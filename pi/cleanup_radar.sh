#!/bin/bash
# ═══════════════════════════════════════════════════════════
# SleepKit – mmWave Rohdaten-Cleanup (täglich via cron)
# ═══════════════════════════════════════════════════════════
# Löscht sensor_radar Rohdaten älter als 30 Tage.
# Minuten-Aggregate (sensor_radar_minute) bleiben unangetastet.
# ═══════════════════════════════════════════════════════════

DB="/opt/sleepkit/data/sleepkit.db"
RETENTION_DAYS=30
LOG="/var/log/sleepkit/radar_cleanup.log"

# Logfile sicherstellen
mkdir -p "$(dirname "$LOG")"

{
    echo "═══ $(date -Iseconds) — Radar-Cleanup gestartet ═══"

    if [[ ! -f "$DB" ]]; then
        echo "FEHLER: Datenbank nicht gefunden: $DB"
        exit 1
    fi

    BEFORE=$(sudo -u monika sqlite3 "$DB" "SELECT COUNT(*) FROM sensor_radar;")
    echo "Rohdaten vor Cleanup: $BEFORE"

    sudo -u monika sqlite3 "$DB" <<SQL
DELETE FROM sensor_radar
 WHERE ts < datetime('now', '-${RETENTION_DAYS} days')
   AND synced = 1;
SQL

    AFTER=$(sudo -u monika sqlite3 "$DB" "SELECT COUNT(*) FROM sensor_radar;")
    DELETED=$((BEFORE - AFTER))
    echo "Rohdaten nach Cleanup: $AFTER (gelöscht: $DELETED)"

    # VACUUM nur wenn was gelöscht wurde — sonst unnötiges I/O
    if [[ $DELETED -gt 0 ]]; then
        echo "VACUUM..."
        sudo -u monika sqlite3 "$DB" "VACUUM;"
        echo "VACUUM abgeschlossen"
    fi

    echo "═══ $(date -Iseconds) — Cleanup beendet ═══"
} >> "$LOG" 2>&1
