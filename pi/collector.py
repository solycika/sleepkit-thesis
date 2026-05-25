#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════
SleepKit – Sensor Collector Daemon (Pi 5 Edition v4)
═══════════════════════════════════════════════════════════
Liest alle Sensoren parallel aus und speichert in SQLite.

Pi-5-Anpassungen v4:
- BME688 jetzt via SPI (Adafruit-Library) statt I²C
  Begründung: I²C clock-stretching Bug auf Pi 5 → Forced-Mode
  Messung wurde nicht durchgeführt. SPI umgeht das Problem.
- CS-Pin auf GPIO 22 (Pin 15), da CE0/CE1 vom Kernel reserviert
- UART-Mapping: MH-Z19C=ttyAMA0, mmWave=ttyAMA2, PMS5003=ttyAMA4 (war ttyAMA3)
  Grund: GPIO 8/9 von SPI gebraucht → PMS5003 auf uart4 (GPIO 12/13)
- I²C-Bus für BH1750 + DS3231 RTC

Globale Sperren:
- _i2c_lock: verhindert parallele I²C-Bus-Zugriffe
- _db_lock: SQLite WAL-Mode mit Lock-Schutz
"""

import json
import logging
import os
import signal
import sqlite3
import struct
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ═══════════════════════════════════════════════════════════
# I²C-Bus Retry-Patch + Global Lock (Pi 5 Workaround)
# ═══════════════════════════════════════════════════════════
import smbus2

_i2c_lock = threading.Lock()

_orig_read_byte_data = smbus2.SMBus.read_byte_data
_orig_write_byte_data = smbus2.SMBus.write_byte_data
_orig_read_i2c_block = smbus2.SMBus.read_i2c_block_data
_orig_write_i2c_block = smbus2.SMBus.write_i2c_block_data
_orig_read_byte = smbus2.SMBus.read_byte
_orig_write_byte = smbus2.SMBus.write_byte

def patched_read_byte_data(self, addr, reg, *args, **kwargs):
    with _i2c_lock:
        for i in range(20):
            try:
                return _orig_read_byte_data(self, addr, reg, *args, **kwargs)
            except OSError:
                time.sleep(0.02)
    raise OSError(f"read_byte_data failed after 20 retries")

def patched_write_byte_data(self, addr, reg, val, *args, **kwargs):
    with _i2c_lock:
        for i in range(20):
            try:
                return _orig_write_byte_data(self, addr, reg, val, *args, **kwargs)
            except OSError:
                time.sleep(0.02)
    raise OSError(f"write_byte_data failed after 20 retries")

def patched_read_i2c_block_data(self, addr, reg, length, *args, **kwargs):
    with _i2c_lock:
        for i in range(20):
            try:
                return _orig_read_i2c_block(self, addr, reg, length, *args, **kwargs)
            except OSError:
                time.sleep(0.02)
    raise OSError(f"read_i2c_block_data failed after 20 retries")

def patched_write_i2c_block_data(self, addr, reg, data, *args, **kwargs):
    with _i2c_lock:
        for i in range(20):
            try:
                return _orig_write_i2c_block(self, addr, reg, data, *args, **kwargs)
            except OSError:
                time.sleep(0.02)
    raise OSError(f"write_i2c_block_data failed after 20 retries")

def patched_read_byte(self, addr, *args, **kwargs):
    with _i2c_lock:
        for i in range(20):
            try:
                return _orig_read_byte(self, addr, *args, **kwargs)
            except OSError:
                time.sleep(0.02)
    raise OSError(f"read_byte failed after 20 retries")

def patched_write_byte(self, addr, val, *args, **kwargs):
    with _i2c_lock:
        for i in range(20):
            try:
                return _orig_write_byte(self, addr, val, *args, **kwargs)
            except OSError:
                time.sleep(0.02)
    raise OSError(f"write_byte failed after 20 retries")

smbus2.SMBus.read_byte_data = patched_read_byte_data
smbus2.SMBus.write_byte_data = patched_write_byte_data
smbus2.SMBus.read_i2c_block_data = patched_read_i2c_block_data
smbus2.SMBus.write_i2c_block_data = patched_write_i2c_block_data
smbus2.SMBus.read_byte = patched_read_byte
smbus2.SMBus.write_byte = patched_write_byte

# ── Globales BME-Init-Done-Event ───────────────────────────
bme_init_done = threading.Event()

# ── Pfade ─────────────────────────────────────────────────
SK_DIR   = Path("/opt/sleepkit")
CFG_FILE = SK_DIR / "config" / "sensors.yaml"
USR_FILE = SK_DIR / "config" / "active_user.json"
DB_FILE  = SK_DIR / "data" / "sleepkit.db"
SCHEMA   = SK_DIR / "src" / "sleepkit_schema.sql"
LOG_DIR  = SK_DIR / "logs"

# ── Logging ───────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)-7s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "collector.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("collector")

# ── Globales Stop-Event ───────────────────────────────────
stop_event = threading.Event()


def signal_handler(sig, frame):
    log.info("Stop-Signal empfangen (%s) – fahre herunter...", sig)
    stop_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ═══════════════════════════════════════════════════════════
# Datenbank
# ═══════════════════════════════════════════════════════════

def init_db() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")

    if SCHEMA.exists():
        conn.executescript(SCHEMA.read_text())
        log.info("DB-Schema angewendet: %s", DB_FILE)
    else:
        log.warning("Schema-Datei nicht gefunden: %s", SCHEMA)

    return conn


_db_lock = threading.Lock()

def db_insert(conn: sqlite3.Connection, table: str, data: dict):
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    with _db_lock:
        try:
            conn.execute(sql, list(data.values()))
            conn.commit()
        except sqlite3.Error as e:
            log.error("DB-Fehler [%s]: %s", table, e)


# ═══════════════════════════════════════════════════════════
# Session-Management
# ═══════════════════════════════════════════════════════════

def load_active_user() -> dict:
    if not USR_FILE.exists():
        log.error("active_user.json nicht gefunden: %s", USR_FILE)
        sys.exit(1)

    with open(USR_FILE) as f:
        user = json.load(f)

    if not user.get("user_id"):
        log.error("Kein user_id in active_user.json gesetzt!")
        sys.exit(1)

    return user


def create_session(conn: sqlite3.Connection, user: dict) -> str:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    db_insert(conn, "sessions", {
        "session_id": session_id,
        "user_id": user["user_id"],
        "user_name": user.get("user_name", "Unbekannt"),
        "started_at": now,
    })

    log.info("Session gestartet: %s (User: %s / %s)",
             session_id, user["user_id"], user.get("user_name"))
    return session_id


def end_session(conn: sqlite3.Connection, session_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
            (now, session_id)
        )
        conn.commit()
    log.info("Session beendet: %s", session_id)


# ═══════════════════════════════════════════════════════════
# Sensor-Treiber
# ═══════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── BME688 via SPI (Adafruit-Library) ──────────────────────
def run_bme688(conn, session_id, cfg):
    """BME688: Temperatur, Feuchte, Druck, Gas. Via SPI auf Pi 5.

    Pi-5-Workaround: I²C clock-stretching macht Forced-Mode unmöglich,
    deshalb wird SPI verwendet. CS-Pin auf GPIO 22 statt CE0,
    weil CE0 vom Kernel-SPI-Driver reserviert wird (lgpio busy).
    """
    log.info("BME688: Starte SPI (Intervall %ss)...", cfg["interval_sec"])
    sensor = None
    try:
        import board
        import digitalio
        import busio
        import adafruit_bme680

        spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

        # CS auf GPIO 22 (Pin 15) — frei, da CE0/CE1 vom Kernel reserviert
        cs_pin_name = cfg.get("cs_pin", "D22")
        cs = digitalio.DigitalInOut(getattr(board, cs_pin_name))

        baudrate = cfg.get("baudrate", 100000)
        sensor = adafruit_bme680.Adafruit_BME680_SPI(spi, cs, baudrate=baudrate)

        # Optional: Sea-Level-Druck für korrekte Höhenmessung
        sensor.sea_level_pressure = cfg.get("sea_level_pressure", 1013.25)

        log.info("BME688: Initialisiert via SPI auf CS=%s", cs_pin_name)
    except Exception as e:
        log.error("BME688: SPI-Init fehlgeschlagen: %s", e)
        bme_init_done.set()
        return

    bme_init_done.set()

    while not stop_event.is_set():
        try:
            data = {
                "session_id": session_id,
                "ts": now_iso(),
                "temperature": round(sensor.temperature, 2),
                "humidity": round(sensor.humidity, 2),
                "pressure": round(sensor.pressure, 2),
                "gas_resistance": round(sensor.gas, 0),
            }
            db_insert(conn, "sensor_bme688", data)
            log.info("BME688: T=%.1f°C H=%.1f%% P=%.1f hPa Gas=%.0fΩ",
                     data["temperature"], data["humidity"],
                     data["pressure"], data["gas_resistance"])
        except Exception as e:
            log.warning("BME688: Lesefehler: %s", e)

        stop_event.wait(cfg["interval_sec"])


# ── BH1750 ────────────────────────────────────────────────
def run_bh1750(conn, session_id, cfg):
    """BH1750: Lichtstärke in Lux."""
    log.info("BH1750: Warte auf BME-Init...")
    bme_init_done.wait(timeout=15)
    log.info("BH1750: Starte (Intervall %ss)...", cfg["interval_sec"])

    bus = None
    addr = cfg.get("address", 0x23)
    CONT_HIRES = 0x10

    for attempt in range(10):
        try:
            bus = smbus2.SMBus(1)
            bus.write_byte(addr, CONT_HIRES)
            time.sleep(0.2)
            log.info("BH1750: Initialisiert auf 0x%02X (Versuch %d)", addr, attempt + 1)
            break
        except Exception as e:
            log.warning("BH1750: Init-Versuch %d fehlgeschlagen: %s", attempt + 1, e)
            bus = None
            time.sleep(0.5)

    if bus is None:
        log.error("BH1750: Alle 10 Init-Versuche fehlgeschlagen – Sensor deaktiviert")
        return

    while not stop_event.is_set():
        try:
            raw = bus.read_i2c_block_data(addr, CONT_HIRES, 2)
            lux = round((raw[0] << 8 | raw[1]) / 1.2, 1)
            db_insert(conn, "sensor_bh1750", {
                "session_id": session_id,
                "ts": now_iso(),
                "lux": lux,
            })
            log.info("BH1750: %.1f lx", lux)
        except Exception as e:
            log.warning("BH1750: Lesefehler: %s", e)

        stop_event.wait(cfg["interval_sec"])


# ── MH-Z19C (CO2) ────────────────────────────────────────
def run_co2(conn, session_id, cfg):
    """MH-Z19C: CO2-Konzentration."""
    warmup = cfg.get("warmup_sec", 180)
    log.info("MH-Z19C: Warte %ss Aufwärmzeit...", warmup)
    stop_event.wait(warmup)
    if stop_event.is_set():
        return

    log.info("MH-Z19C: Starte (Intervall %ss)...", cfg["interval_sec"])
    try:
        import mh_z19
        log.info("MH-Z19C: Initialisiert auf %s", cfg.get("device", "/dev/serial0"))
    except Exception as e:
        log.error("MH-Z19C: Init fehlgeschlagen: %s", e)
        return

    while not stop_event.is_set():
        try:
            result = mh_z19.read_all()
            if result and "co2" in result:
                db_insert(conn, "sensor_co2", {
                    "session_id": session_id,
                    "ts": now_iso(),
                    "co2_ppm": result["co2"],
                    "temperature": result.get("temperature"),
                })
                log.info("MH-Z19C: CO2=%d ppm", result["co2"])
        except Exception as e:
            log.warning("MH-Z19C: Lesefehler: %s", e)

        stop_event.wait(cfg["interval_sec"])


# ── PMS5003 (Feinstaub) ──────────────────────────────────
def run_dust(conn, session_id, cfg):
    """PMS5003: PM1.0, PM2.5, PM10 in µg/m³.

    Pi-5-Hinweis: Läuft auf uart4 (GPIO 12/13 = Pin 32/33) statt uart3,
    weil GPIO 8/9 von SPI für BME688 belegt sind.
    """
    log.info("PMS5003: Starte (Intervall %ss)...", cfg["interval_sec"])
    try:
        import serial
        ser = serial.Serial(
            cfg.get("device", "/dev/ttyAMA4"),
            baudrate=cfg.get("baudrate", 9600),
            timeout=2
        )
        log.info("PMS5003: Initialisiert auf %s", cfg.get("device"))
    except Exception as e:
        log.error("PMS5003: Init fehlgeschlagen: %s", e)
        return

    def read_frame():
        for _ in range(64):
            b = ser.read(1)
            if not b:
                return None
            if b[0] == 0x42:
                b2 = ser.read(1)
                if b2 and b2[0] == 0x4D:
                    rest = ser.read(30)
                    if len(rest) == 30:
                        frame = b'\x42\x4d' + rest
                        chk = sum(frame[:30]) & 0xFFFF
                        actual = (frame[30] << 8) | frame[31]
                        if chk == actual:
                            return {
                                "pm1_0": (frame[4] << 8) | frame[5],
                                "pm2_5": (frame[6] << 8) | frame[7],
                                "pm10":  (frame[8] << 8) | frame[9],
                            }
        return None

    while not stop_event.is_set():
        try:
            reading = read_frame()
            if reading:
                db_insert(conn, "sensor_dust", {
                    "session_id": session_id,
                    "ts": now_iso(),
                    **reading,
                })
                log.info("PMS5003: PM1=%d PM2.5=%d PM10=%d µg/m³",
                         reading["pm1_0"], reading["pm2_5"], reading["pm10"])
            else:
                log.warning("PMS5003: Kein Frame empfangen")
        except Exception as e:
            log.warning("PMS5003: Lesefehler: %s", e)

        stop_event.wait(cfg["interval_sec"])

    ser.close()


# ── mmWave Radar S3KM1110 (ASCII-Output) ─────────────────
def run_radar(conn, session_id, cfg):
    """Waveshare mmWave: ASCII 'ON/OFF/Range XX' Output.

    Schreibt Rohdaten in sensor_radar (bleiben lokal auf dem Pi,
    Retention 30 Tage) UND Minuten-Aggregate in sensor_radar_minute
    (gehen zur Cloud). Methodisch begründet durch Thesis 6.4.3:
    'Anwesenheitsanteil im Schlaffenster' = Minuten-Granularität.
    """
    log.info("mmWave: Starte (Intervall %ss, mit Minuten-Aggregation)...",
             cfg["interval_sec"])
    try:
        import serial
        ser = serial.Serial(
            cfg.get("device", "/dev/ttyAMA2"),
            baudrate=cfg.get("baudrate", 115200),
            timeout=1
        )
        log.info("mmWave: Initialisiert auf %s", cfg.get("device"))
    except Exception as e:
        log.error("mmWave: Init fehlgeschlagen: %s", e)
        return

    # Minuten-Puffer: sammelt Samples bis zum Minuten-Wechsel
    current_minute = None       # str, "YYYY-MM-DDTHH:MM"
    buf_samples = []            # list[dict] mit presence, distance_cm

    def flush_minute(minute_key, samples):
        """Aggregiert Puffer und schreibt eine Zeile in sensor_radar_minute."""
        if not samples:
            return
        n = len(samples)
        n_presence = sum(1 for s in samples if s["presence"] is not None
                                            and s["presence"] >= 1)
        n_movement = sum(1 for s in samples if s["presence"] == 2)
        distances = [s["distance_cm"] for s in samples
                     if s["distance_cm"] is not None]

        row = {
            "session_id":     session_id,
            "ts_minute":      minute_key + ":00",
            "n_samples":      n,
            "presence_share": round(n_presence / n, 3),
            "movement_share": round(n_movement / n, 3),
            "distance_avg":   round(sum(distances) / len(distances), 1)
                              if distances else None,
            "distance_min":   min(distances) if distances else None,
            "distance_max":   max(distances) if distances else None,
        }
        try:
            db_insert(conn, "sensor_radar_minute", row)
            log.info("mmWave-Minute %s: n=%d, presence=%.2f, movement=%.2f",
                     minute_key, n, row["presence_share"],
                     row["movement_share"])
        except Exception as e:
            # UNIQUE-Constraint kann beim Wiederanlauf greifen — ignorieren
            log.debug("mmWave-Minute Insert übersprungen: %s", e)

    serial_buf = b""
    while not stop_event.is_set():
        try:
            data = ser.read(128)
            if data:
                serial_buf += data
                while b"\r\n" in serial_buf:
                    line, serial_buf = serial_buf.split(b"\r\n", 1)
                    txt = line.decode("ascii", errors="ignore").strip()

                    presence = None
                    distance = None
                    if txt == "ON":
                        presence = 2
                    elif txt == "OFF":
                        presence = 0
                    elif txt.startswith("Range"):
                        try:
                            distance = int(txt.split()[1])
                            presence = 1
                        except (IndexError, ValueError):
                            pass

                    if presence is not None or distance is not None:
                        # 1. Rohdaten schreiben (bleiben lokal auf dem Pi)
                        ts = now_iso()
                        db_insert(conn, "sensor_radar", {
                            "session_id": session_id,
                            "ts": ts,
                            "presence": presence if presence is not None else 1,
                            "distance_cm": distance,
                            "energy": None,
                        })

                        # 2. Minuten-Aggregation
                        minute_key = ts[:16]   # "YYYY-MM-DDTHH:MM"
                        if current_minute is None:
                            current_minute = minute_key
                        elif minute_key != current_minute:
                            # Minute hat gewechselt → flush
                            flush_minute(current_minute, buf_samples)
                            buf_samples = []
                            current_minute = minute_key

                        buf_samples.append({
                            "presence":    presence,
                            "distance_cm": distance,
                        })

                        log.debug("mmWave: %s (Distanz=%s)", txt, distance)
        except Exception as e:
            log.warning("mmWave: Lesefehler: %s", e)

        stop_event.wait(cfg["interval_sec"])

    # Beim Stop: letzten Puffer flushen
    if buf_samples and current_minute:
        flush_minute(current_minute, buf_samples)

    ser.close()


# ── INMP441 Mikrofone (I2S Audio) ────────────────────────
def run_audio(conn, session_id, cfg):
    """INMP441: Geräuschpegel und Schnarch-Erkennung."""
    log.info("INMP441: Starte (Intervall %ss)...", cfg["interval_sec"])
    try:
        import sounddevice as sd
        import numpy as np

        sample_rate = cfg.get("sample_rate", 48000)
        channels = cfg.get("channels", 2)
        window = cfg.get("analysis_window_sec", 5)

        devices = sd.query_devices()
        log.info("INMP441: Verfügbare Audio-Geräte:\n%s", devices)

        device_id = None
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) >= 2:
                name = d.get("name", "").lower()
                if "i2s" in name or "google" in name or "snd" in name:
                    device_id = i
                    log.info("INMP441: Verwende Gerät %d: %s", i, d["name"])
                    break

        if device_id is None:
            device_id = sd.default.device[0]
            log.warning("INMP441: Kein I2S-Gerät gefunden, nutze Default: %s", device_id)

    except Exception as e:
        log.error("INMP441: Init fehlgeschlagen: %s", e)
        return

    while not stop_event.is_set():
        try:
            audio = sd.rec(
                int(sample_rate * window),
                samplerate=sample_rate,
                channels=channels,
                device=device_id,
                dtype="float32"
            )
            sd.wait()

            rms_l = float(np.sqrt(np.mean(audio[:, 0] ** 2)))
            peak_l = float(np.max(np.abs(audio[:, 0])))

            rms_r = 0.0
            peak_r = 0.0
            if channels >= 2 and audio.shape[1] >= 2:
                rms_r = float(np.sqrt(np.mean(audio[:, 1] ** 2)))
                peak_r = float(np.max(np.abs(audio[:, 1])))

            snore_score = 0.0
            try:
                from scipy.fft import rfft, rfftfreq
                mono = audio[:, 0]
                fft_vals = np.abs(rfft(mono))
                freqs = rfftfreq(len(mono), 1.0 / sample_rate)
                mask = (freqs >= 100) & (freqs <= 300)
                snore_energy = np.sum(fft_vals[mask] ** 2)
                total_energy = np.sum(fft_vals ** 2)
                if total_energy > 0:
                    snore_score = round(float(snore_energy / total_energy), 4)
            except ImportError:
                pass

            db_insert(conn, "sensor_audio", {
                "session_id": session_id,
                "ts": now_iso(),
                "rms_left": round(rms_l, 6),
                "rms_right": round(rms_r, 6),
                "peak_left": round(peak_l, 6),
                "peak_right": round(peak_r, 6),
                "snore_score": snore_score,
            })
            log.info("INMP441: RMS_L=%.4f RMS_R=%.4f Snore=%.3f",
                     rms_l, rms_r, snore_score)

        except Exception as e:
            log.warning("INMP441: Aufnahme-Fehler: %s", e)

        stop_event.wait(cfg["interval_sec"])


# ═══════════════════════════════════════════════════════════
# Sensor → Thread Zuordnung
# Reihenfolge: BME zuerst (andere I²C warten auf bme_init_done)
# ═══════════════════════════════════════════════════════════

SENSOR_RUNNERS = [
    ("bme688",  run_bme688),
    ("bh1750",  run_bh1750),
    ("mhz19c",  run_co2),
    ("pms5003", run_dust),
    ("mmwave",  run_radar),
    ("inmp441", run_audio),
]


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("SleepKit Collector gestartet (Pi 5 Edition v4 – BME via SPI)")
    log.info("=" * 60)

    if not CFG_FILE.exists():
        log.error("Konfiguration nicht gefunden: %s", CFG_FILE)
        sys.exit(1)

    with open(CFG_FILE) as f:
        config = yaml.safe_load(f)

    sensors_cfg = config.get("sensors", {})

    user = load_active_user()
    log.info("Aktiver User: %s (%s)", user["user_name"], user["user_id"])

    conn = init_db()
    session_id = create_session(conn, user)

    if not sensors_cfg.get("bme688", {}).get("enabled", False):
        bme_init_done.set()

    threads = []
    for name, runner in SENSOR_RUNNERS:
        cfg = sensors_cfg.get(name, {})
        if not cfg.get("enabled", False):
            log.info("Sensor '%s' deaktiviert – überspringe", name)
            continue

        t = threading.Thread(
            target=runner,
            args=(conn, session_id, cfg),
            name=f"sensor-{name}",
            daemon=True,
        )
        t.start()
        threads.append(t)
        log.info("Thread gestartet: %s", t.name)
        if name == "bme688":
            time.sleep(0.5)  # BME-SPI-Init Zeit geben

    if not threads:
        log.error("Keine Sensoren aktiviert! Prüfe sensors.yaml")
        sys.exit(1)

    log.info("Alle %d Sensor-Threads laufen. Warte auf Stop-Signal...", len(threads))

    try:
        while not stop_event.is_set():
            stop_event.wait(1)
    except KeyboardInterrupt:
        stop_event.set()

    log.info("Fahre herunter...")
    end_session(conn, session_id)

    for t in threads:
        t.join(timeout=5)

    conn.close()
    log.info("SleepKit Collector beendet.")


if __name__ == "__main__":
    main()
