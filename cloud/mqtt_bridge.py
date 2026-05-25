#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════
SleepKit – MQTT-to-PostgreSQL Bridge (EC2-Server)
═══════════════════════════════════════════════════════════
Subscribt auf alle sleepkit/data/# Topics und schreibt
die empfangenen Sensordaten in die PostgreSQL-Datenbank.

Läuft als systemd-Service auf der EC2-Instanz.

Verwendung:
  source /opt/sleepkit-api/venv/bin/activate
  python3 /opt/sleepkit-api/mqtt_bridge.py

Konfiguration: /opt/sleepkit-api/config.yaml
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

import yaml

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("FEHLER: pip install paho-mqtt")
    sys.exit(1)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("FEHLER: pip install psycopg2-binary")
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)-7s %(message)s",
    handlers=[
        logging.FileHandler("/var/log/sleepkit/mqtt_bridge.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("mqtt-bridge")

# ── Stop-Event ────────────────────────────────────────────
stop_event = threading.Event()

def signal_handler(sig, frame):
    log.info("Stop-Signal empfangen – fahre herunter...")
    stop_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ═══════════════════════════════════════════════════════════
# Konfiguration
# ═══════════════════════════════════════════════════════════

CONFIG_FILE = os.environ.get(
    "SLEEPKIT_CONFIG", "/opt/sleepkit-api/config.yaml"
)

def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


# ═══════════════════════════════════════════════════════════
# PostgreSQL
# ═══════════════════════════════════════════════════════════

# Erlaubte Tabellen und ihre Spalten (Whitelist gegen Injection)
TABLE_COLUMNS = {
    "sessions": [
        "session_id", "user_id", "user_name", "started_at", "ended_at",
    ],
    "sensor_bme688": [
        "session_id", "ts", "temperature", "humidity", "pressure",
        "gas_resistance",
    ],
    "sensor_bh1750": [
        "session_id", "ts", "lux",
    ],
    "sensor_co2": [
        "session_id", "ts", "co2_ppm", "temperature",
    ],
    "sensor_dust": [
        "session_id", "ts", "pm1_0", "pm2_5", "pm10",
    ],
    "sensor_radar": [
        "session_id", "ts", "presence", "distance_cm", "energy",
    ],
    "sensor_radar_minute": [
        "session_id", "ts_minute", "n_samples",
        "presence_share", "movement_share",
        "distance_avg", "distance_min", "distance_max",
    ],
    "sensor_audio": [
        "session_id", "ts", "rms_left", "rms_right", "peak_left",
        "peak_right", "snore_score",
    ],
}


def get_pg_connection(cfg: dict):
    """PostgreSQL-Verbindung erstellen."""
    db_cfg = cfg.get("database", {})
    return psycopg2.connect(
        host=db_cfg.get("host", "localhost"),
        port=db_cfg.get("port", 5432),
        dbname=db_cfg.get("name", "sleepkit_db"),
        user=db_cfg.get("user", "sleepkit"),
        password=db_cfg.get("password", ""),
    )


def insert_rows(pg_conn, table: str, rows: list[dict]) -> int:
    """Zeilen in PostgreSQL einfügen. Gibt Anzahl zurück."""
    if table not in TABLE_COLUMNS:
        log.warning("Unbekannte Tabelle: %s – ignoriert", table)
        return 0

    allowed_cols = TABLE_COLUMNS[table]
    inserted = 0

    with pg_conn.cursor() as cur:
        for row in rows:
            # Nur erlaubte Spalten übernehmen
            filtered = {k: v for k, v in row.items() if k in allowed_cols}
            if not filtered:
                continue

            cols = list(filtered.keys())
            vals = list(filtered.values())
            placeholders = ", ".join(["%s"] * len(cols))
            col_str = ", ".join(cols)

            # UPSERT: Bei sessions auf session_id, bei Sensoren ignorieren
            if table == "sessions":
                sql = (
                    f"INSERT INTO {table} ({col_str}, synced_at) "
                    f"VALUES ({placeholders}, NOW()) "
                    f"ON CONFLICT (session_id) DO UPDATE SET "
                    f"ended_at = EXCLUDED.ended_at, synced_at = NOW()"
                )
            else:
                # Sensordaten: Duplikate via session_id+ts vermeiden
                sql = (
                    f"INSERT INTO {table} ({col_str}, synced_at) "
                    f"VALUES ({placeholders}, NOW()) "
                    f"ON CONFLICT DO NOTHING"
                )

            try:
                cur.execute(sql, vals)
                inserted += cur.rowcount
            except Exception as e:
                log.error("INSERT-Fehler [%s]: %s", table, e)
                pg_conn.rollback()
                return inserted

    pg_conn.commit()
    return inserted


# ═══════════════════════════════════════════════════════════
# MQTT Callbacks
# ═══════════════════════════════════════════════════════════

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("MQTT verbunden – subscribing...")
        cfg = userdata["config"]
        prefix = cfg.get("mqtt", {}).get("topic_prefix", "sleepkit")
        client.subscribe(f"{prefix}/data/#", qos=1)
        client.subscribe(f"{prefix}/status/#", qos=1)
        log.info("Subscribed: %s/data/# und %s/status/#", prefix, prefix)
    else:
        log.error("MQTT Verbindung fehlgeschlagen: rc=%d", rc)


def on_message(client, userdata, msg):
    """Empfangene MQTT-Nachricht verarbeiten."""
    topic = msg.topic
    log.debug("Nachricht auf %s (%d bytes)", topic, len(msg.payload))

    # Status-Nachrichten nur loggen
    if "/status/" in topic:
        try:
            status = json.loads(msg.payload)
            log.info("Pi-Status: %s → %s", topic, status.get("status"))
        except Exception:
            pass
        return

    # Sensordaten verarbeiten
    if "/data/" not in topic:
        return

    try:
        payload = json.loads(msg.payload)
    except json.JSONDecodeError as e:
        log.error("JSON-Fehler auf %s: %s", topic, e)
        return

    table = payload.get("table")
    rows = payload.get("rows", [])
    count = payload.get("count", 0)

    if not table or not rows:
        log.warning("Leere Nachricht auf %s", topic)
        return

    log.info("Empfangen: %d Zeilen für %s", count, table)

    # In PostgreSQL schreiben
    try:
        pg_conn = userdata["pg_conn"]
        # Verbindung prüfen / neu aufbauen
        if pg_conn.closed:
            log.info("PostgreSQL Reconnect...")
            userdata["pg_conn"] = get_pg_connection(userdata["config"])
            pg_conn = userdata["pg_conn"]

        inserted = insert_rows(pg_conn, table, rows)
        log.info("PostgreSQL: %d/%d Zeilen eingefügt in %s",
                 inserted, count, table)

    except Exception as e:
        log.error("PostgreSQL-Fehler: %s", e)
        # Verbindung zurücksetzen
        try:
            userdata["pg_conn"] = get_pg_connection(userdata["config"])
        except Exception:
            log.error("PostgreSQL Reconnect fehlgeschlagen!")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("SleepKit MQTT-to-PostgreSQL Bridge gestartet")
    log.info("=" * 60)

    # Konfiguration laden
    cfg = load_config()
    mqtt_cfg = cfg.get("mqtt", {})

    # PostgreSQL-Verbindung
    try:
        pg_conn = get_pg_connection(cfg)
        log.info("PostgreSQL verbunden")
    except Exception as e:
        log.error("PostgreSQL Verbindung fehlgeschlagen: %s", e)
        sys.exit(1)

    # MQTT-Client erstellen
    client = mqtt.Client(
        client_id="sleepkit-bridge",
        protocol=mqtt.MQTTv311,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )

    # Userdata für Callbacks
    client.user_data_set({
        "config": cfg,
        "pg_conn": pg_conn,
    })

    # Auth
    client.username_pw_set(
        mqtt_cfg.get("bridge_username", "sleepkit-bridge"),
        mqtt_cfg.get("bridge_password", ""),
    )

    # Callbacks
    client.on_connect = on_connect
    client.on_message = on_message

    # Verbinden (localhost, da Mosquitto auf derselben EC2 läuft)
    broker_host = mqtt_cfg.get("local_host", "localhost")
    broker_port = mqtt_cfg.get("local_port", 1883)

    try:
        client.connect(broker_host, broker_port, keepalive=60)
        log.info("MQTT Bridge lauscht auf %s:%d", broker_host, broker_port)
    except Exception as e:
        log.error("MQTT Verbindungsfehler: %s", e)
        sys.exit(1)

    # Event-Loop
    client.loop_start()

    try:
        while not stop_event.is_set():
            stop_event.wait(1)
    except KeyboardInterrupt:
        stop_event.set()

    # Aufräumen
    log.info("Fahre herunter...")
    client.loop_stop()
    client.disconnect()
    pg_conn.close()
    log.info("MQTT Bridge beendet.")


if __name__ == "__main__":
    main()
