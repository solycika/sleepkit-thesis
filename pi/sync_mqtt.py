#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════
SleepKit – MQTT Sync Service
═══════════════════════════════════════════════════════════
Liest unsynced Datensätze aus der lokalen SQLite-Datenbank
und überträgt sie per MQTT an den Mosquitto-Broker auf der
EC2-Instanz. Läuft als zweiter systemd-Service.

Voraussetzung:
  - Collector-Service schreibt Sensordaten in SQLite
  - Mosquitto-Broker läuft auf der EC2 (Port 8883/TLS)
  - MQTT-Credentials in /opt/sleepkit/config/mqtt.yaml

Verwendung:
  source /opt/sleepkit/venv/bin/activate
  python3 /opt/sleepkit/src/sync_mqtt.py

Konfiguration: /opt/sleepkit/config/mqtt.yaml
Datenbank:     /opt/sleepkit/data/sleepkit.db
"""

import json
import logging
import os
import signal
import sqlite3
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("FEHLER: paho-mqtt nicht installiert!")
    print("  source /opt/sleepkit/venv/bin/activate")
    print("  pip install paho-mqtt")
    sys.exit(1)

# ── Pfade ─────────────────────────────────────────────────
SK_DIR    = Path("/opt/sleepkit")
MQTT_CFG  = SK_DIR / "config" / "mqtt.yaml"
DB_FILE   = SK_DIR / "data" / "sleepkit.db"
LOG_DIR   = SK_DIR / "logs"

# ── Logging ───────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)-7s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "sync_mqtt.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("sync-mqtt")

# ── Globales Stop-Event ───────────────────────────────────
stop_event = threading.Event()


def signal_handler(sig, frame):
    log.info("Stop-Signal empfangen (%s) – fahre herunter...", sig)
    stop_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ═══════════════════════════════════════════════════════════
# Konfiguration
# ═══════════════════════════════════════════════════════════

def load_mqtt_config() -> dict:
    """MQTT-Konfiguration aus mqtt.yaml laden."""
    if not MQTT_CFG.exists():
        log.error("MQTT-Konfiguration nicht gefunden: %s", MQTT_CFG)
        log.error("Bitte mqtt.yaml erstellen (siehe Doku).")
        sys.exit(1)

    with open(MQTT_CFG) as f:
        cfg = yaml.safe_load(f)

    mqtt_cfg = cfg.get("mqtt", {})

    # Pflichtfelder prüfen
    required = ["broker_host", "username", "password"]
    for key in required:
        if not mqtt_cfg.get(key):
            log.error("Pflichtfeld '%s' fehlt in mqtt.yaml!", key)
            sys.exit(1)

    # Defaults setzen
    mqtt_cfg.setdefault("broker_port", 8883)
    mqtt_cfg.setdefault("use_tls", True)
    mqtt_cfg.setdefault("topic_prefix", "sleepkit")
    mqtt_cfg.setdefault("batch_size", 200)
    mqtt_cfg.setdefault("sync_interval_sec", 30)
    mqtt_cfg.setdefault("qos", 1)
    mqtt_cfg.setdefault("client_id", f"sleepkit-pi-{os.uname().nodename}")

    return mqtt_cfg


# ═══════════════════════════════════════════════════════════
# MQTT-Client
# ═══════════════════════════════════════════════════════════

class SyncClient:
    """MQTT-Client für die Datenübertragung."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.connected = False
        self._lock = threading.Lock()

        # Paho MQTT Client (v2 API)
        self.client = mqtt.Client(
            client_id=cfg["client_id"],
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

        # Authentifizierung
        self.client.username_pw_set(
            cfg["username"],
            cfg["password"]
        )

        # TLS konfigurieren
        if cfg.get("use_tls", True):
            tls_ctx = ssl.create_default_context()
            # Falls eigenes CA-Zertifikat vorhanden:
            ca_cert = cfg.get("ca_cert")
            if ca_cert and Path(ca_cert).exists():
                tls_ctx.load_verify_locations(ca_cert)
            self.client.tls_set_context(tls_ctx)

        # Callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish

        # Last Will: offline-Status melden
        self.client.will_set(
            f"{cfg['topic_prefix']}/status/{cfg['client_id']}",
            payload=json.dumps({"status": "offline"}),
            qos=1,
            retain=True,
        )

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.connected = True
            log.info("MQTT verbunden mit %s:%d",
                     self.cfg["broker_host"], self.cfg["broker_port"])
            # Online-Status publizieren
            self.client.publish(
                f"{self.cfg['topic_prefix']}/status/{self.cfg['client_id']}",
                payload=json.dumps({
                    "status": "online",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }),
                qos=1,
                retain=True,
            )
        else:
            self.connected = False
            reasons = {
                1: "Falsche Protokollversion",
                2: "Client-ID abgelehnt",
                3: "Broker nicht erreichbar",
                4: "Falsche Credentials",
                5: "Nicht autorisiert",
            }
            log.error("MQTT Verbindung fehlgeschlagen: %s (rc=%d)",
                      reasons.get(rc, "Unbekannt"), rc)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self.connected = False
        if rc != 0:
            log.warning("MQTT unerwartet getrennt (rc=%d). Reconnect...", rc)
        else:
            log.info("MQTT sauber getrennt.")

    def _on_publish(self, client, userdata, mid, rc=None, properties=None):
        log.debug("MQTT Nachricht veröffentlicht (mid=%d)", mid)

    def connect(self) -> bool:
        """Verbindung zum Broker aufbauen."""
        try:
            self.client.connect(
                self.cfg["broker_host"],
                self.cfg["broker_port"],
                keepalive=60,
            )
            self.client.loop_start()
            # Warte kurz auf Verbindung
            for _ in range(10):
                if self.connected:
                    return True
                time.sleep(0.5)
            log.warning("MQTT Verbindungs-Timeout nach 5s")
            return False
        except Exception as e:
            log.error("MQTT Verbindungsfehler: %s", e)
            return False

    def disconnect(self):
        """Sauber trennen."""
        self.client.loop_stop()
        self.client.disconnect()

    def publish_batch(self, table: str, rows: list[dict]) -> bool:
        """Einen Batch Sensordaten per MQTT publizieren."""
        if not self.connected:
            log.warning("MQTT nicht verbunden – Batch übersprungen")
            return False

        topic = f"{self.cfg['topic_prefix']}/data/{table}"
        payload = json.dumps({
            "table": table,
            "count": len(rows),
            "rows": rows,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        })

        try:
            result = self.client.publish(
                topic,
                payload=payload,
                qos=self.cfg["qos"],
            )
            result.wait_for_publish(timeout=10)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                log.info("MQTT: %d Zeilen → %s veröffentlicht", len(rows), topic)
                return True
            else:
                log.error("MQTT Publish fehlgeschlagen: rc=%d", result.rc)
                return False
        except Exception as e:
            log.error("MQTT Publish-Fehler: %s", e)
            return False


# ═══════════════════════════════════════════════════════════
# SQLite Sync-Logik
# ═══════════════════════════════════════════════════════════

# Tabellen, die synchronisiert werden.
# sensor_radar (Rohdaten alle 0.5s) wird NICHT mehr gesynct — bleibt lokal
# auf dem Pi mit 30 Tage Retention. Stattdessen kommt sensor_radar_minute
# (Minuten-Aggregate) zur Cloud. Methodisch siehe Thesis 6.4.3.
SYNC_TABLES = [
    "sessions",
    "sensor_bme688",
    "sensor_bh1750",
    "sensor_co2",
    "sensor_dust",
    "sensor_radar_minute",
    "sensor_audio",
]


def get_db_connection() -> sqlite3.Connection:
    """SQLite-Verbindung mit Row-Factory."""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def fetch_unsynced(conn: sqlite3.Connection, table: str,
                   batch_size: int) -> list[dict]:
    """Unsynced Zeilen aus einer Tabelle lesen."""
    try:
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE synced = 0 "
            f"ORDER BY rowid ASC LIMIT ?",
            (batch_size,)
        )
        rows = [dict(row) for row in cursor.fetchall()]
        return rows
    except sqlite3.Error as e:
        log.error("DB-Lesefehler [%s]: %s", table, e)
        return []


def mark_synced(conn: sqlite3.Connection, table: str,
                rows: list[dict]):
    """Zeilen als synced=1 markieren."""
    if not rows:
        return

    # Verwende 'id' als Primary Key (sessions nutzt 'session_id')
    if table == "sessions":
        ids = [row["session_id"] for row in rows]
        placeholders = ",".join(["?"] * len(ids))
        sql = f"UPDATE {table} SET synced = 1 WHERE session_id IN ({placeholders})"
    else:
        ids = [row["id"] for row in rows]
        placeholders = ",".join(["?"] * len(ids))
        sql = f"UPDATE {table} SET synced = 1 WHERE id IN ({placeholders})"

    try:
        conn.execute(sql, ids)
        conn.commit()
        log.debug("Synced: %d Zeilen in %s", len(ids), table)
    except sqlite3.Error as e:
        log.error("DB-Update-Fehler [%s]: %s", table, e)


def log_sync(conn: sqlite3.Connection, table: str,
             count: int, status: str, detail: str = None):
    """Sync-Ergebnis in sync_log schreiben."""
    try:
        conn.execute(
            "INSERT INTO sync_log (ts, table_name, rows_synced, status, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), table, count, status, detail)
        )
        conn.commit()
    except sqlite3.Error as e:
        log.error("Sync-Log-Fehler: %s", e)


# ═══════════════════════════════════════════════════════════
# Netzwerk-Check
# ═══════════════════════════════════════════════════════════

def check_network() -> bool:
    """Prüft, ob eine Netzwerkverbindung besteht."""
    import subprocess
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "3", "8.8.8.8"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
# Haupt-Sync-Loop
# ═══════════════════════════════════════════════════════════

def sync_loop(mqtt_client: SyncClient, cfg: dict):
    """Haupt-Sync-Schleife: Prüft periodisch auf unsynced Daten."""
    batch_size = cfg.get("batch_size", 200)
    interval = cfg.get("sync_interval_sec", 30)

    log.info("Sync-Loop gestartet (Intervall: %ds, Batch: %d)",
             interval, batch_size)

    while not stop_event.is_set():
        # 1. Netzwerk prüfen
        if not check_network():
            log.debug("Kein Netzwerk – warte...")
            stop_event.wait(interval)
            continue

        # 2. MQTT-Verbindung prüfen / aufbauen
        if not mqtt_client.connected:
            log.info("Versuche MQTT-Reconnect...")
            if not mqtt_client.connect():
                stop_event.wait(interval)
                continue

        # 3. Daten synchronisieren
        conn = get_db_connection()
        total_synced = 0

        try:
            for table in SYNC_TABLES:
                if stop_event.is_set():
                    break

                rows = fetch_unsynced(conn, table, batch_size)
                if not rows:
                    continue

                # synced-Feld aus den Zeilen entfernen (nicht mitsenden)
                clean_rows = []
                for row in rows:
                    r = dict(row)
                    r.pop("synced", None)
                    clean_rows.append(r)

                # Per MQTT publizieren
                success = mqtt_client.publish_batch(table, clean_rows)

                if success:
                    mark_synced(conn, table, rows)
                    log_sync(conn, table, len(rows), "ok")
                    total_synced += len(rows)
                else:
                    log_sync(conn, table, 0, "error",
                             "MQTT Publish fehlgeschlagen")

            if total_synced > 0:
                log.info("Sync-Runde: %d Zeilen übertragen", total_synced)

        except Exception as e:
            log.error("Sync-Fehler: %s", e)
        finally:
            conn.close()

        # 4. Warten
        stop_event.wait(interval)


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("SleepKit MQTT Sync Service gestartet")
    log.info("=" * 60)

    # Konfiguration laden
    cfg = load_mqtt_config()
    log.info("Broker: %s:%d (TLS=%s)",
             cfg["broker_host"], cfg["broker_port"], cfg.get("use_tls"))
    log.info("Topic-Prefix: %s", cfg["topic_prefix"])
    log.info("Client-ID: %s", cfg["client_id"])

    # Prüfe ob Datenbank existiert
    if not DB_FILE.exists():
        log.error("Datenbank nicht gefunden: %s", DB_FILE)
        log.error("Collector muss zuerst laufen!")
        sys.exit(1)

    # MQTT-Client erstellen
    mqtt_client = SyncClient(cfg)

    # Erste Verbindung versuchen
    if check_network():
        log.info("Netzwerk verfügbar – verbinde mit Broker...")
        mqtt_client.connect()
    else:
        log.info("Kein Netzwerk – starte im Offline-Modus...")

    # Sync-Loop starten
    try:
        sync_loop(mqtt_client, cfg)
    except KeyboardInterrupt:
        stop_event.set()

    # Aufräumen
    log.info("Fahre herunter...")
    mqtt_client.disconnect()
    log.info("SleepKit MQTT Sync Service beendet.")


if __name__ == "__main__":
    main()
