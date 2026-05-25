-- ═══════════════════════════════════════════════════════════
-- SleepKit – SQLite Schema (lokale Offline-Datenbank)
-- Datei: /opt/sleepkit/data/sleepkit.db
-- ═══════════════════════════════════════════════════════════

-- Messungs-Sessions (pro User, pro Koffer-Einsatz)
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,           -- UUID
    user_id      TEXT NOT NULL,              -- z.B. "user01"
    user_name    TEXT NOT NULL,
    started_at   TEXT NOT NULL,              -- ISO 8601
    ended_at     TEXT,                       -- NULL = läuft noch
    synced       INTEGER DEFAULT 0           -- 0=nein, 1=ja
);

-- BME688: Temperatur, Feuchte, Druck, Gas
CREATE TABLE IF NOT EXISTS sensor_bme688 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts           TEXT NOT NULL,              -- ISO 8601 Zeitstempel
    temperature  REAL,                       -- °C
    humidity     REAL,                       -- %
    pressure     REAL,                       -- hPa
    gas_resistance REAL,                     -- Ohm
    synced       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bme688_session ON sensor_bme688(session_id, ts);

-- BH1750: Licht
CREATE TABLE IF NOT EXISTS sensor_bh1750 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts           TEXT NOT NULL,
    lux          REAL,                       -- Lux
    synced       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bh1750_session ON sensor_bh1750(session_id, ts);

-- MH-Z19C: CO2
CREATE TABLE IF NOT EXISTS sensor_co2 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts           TEXT NOT NULL,
    co2_ppm      INTEGER,                    -- ppm
    temperature  REAL,                       -- °C (Sensor-intern)
    synced       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_co2_session ON sensor_co2(session_id, ts);

-- PMS5003: Feinstaub
CREATE TABLE IF NOT EXISTS sensor_dust (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts           TEXT NOT NULL,
    pm1_0        INTEGER,                    -- µg/m³
    pm2_5        INTEGER,                    -- µg/m³
    pm10         INTEGER,                    -- µg/m³
    synced       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dust_session ON sensor_dust(session_id, ts);

-- mmWave Radar: Präsenz & Bewegung
CREATE TABLE IF NOT EXISTS sensor_radar (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts           TEXT NOT NULL,
    presence     INTEGER,                    -- 0=niemand, 1=statisch, 2=Bewegung
    distance_cm  INTEGER,                    -- Entfernung in cm
    energy       INTEGER,                    -- Rohwert (optional)
    synced       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_radar_session ON sensor_radar(session_id, ts);

-- INMP441: Audio/Geräuschpegel
CREATE TABLE IF NOT EXISTS sensor_audio (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts           TEXT NOT NULL,
    rms_left     REAL,                       -- RMS Linker Kanal
    rms_right    REAL,                       -- RMS Rechter Kanal
    peak_left    REAL,                       -- Peak Linker Kanal
    peak_right   REAL,                       -- Peak Rechter Kanal
    snore_score  REAL,                       -- 0.0–1.0 (Schnarch-Indikator)
    synced       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audio_session ON sensor_audio(session_id, ts);

-- Sync-Tracking: welche Batches wurden per MQTT übertragen?
CREATE TABLE IF NOT EXISTS sync_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    rows_synced  INTEGER,
    status       TEXT,                       -- "ok" / "error"
    detail       TEXT
);
