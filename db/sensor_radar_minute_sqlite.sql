-- ═══════════════════════════════════════════════════════════
-- SleepKit – mmWave Minuten-Aggregat (SQLite, Pi-Seite)
-- ═══════════════════════════════════════════════════════════
-- Aggregiert pro Minute die ~120 Roh-Samples des mmWave-Radars
-- aus sensor_radar. Diese Tabelle wird zur Cloud gesynct,
-- sensor_radar (Rohdaten) bleibt lokal auf dem Pi (30 Tage).
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sensor_radar_minute (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    ts_minute       TEXT NOT NULL,         -- ISO 8601, auf Minute gerundet
    n_samples       INTEGER NOT NULL,      -- Anzahl Roh-Samples in der Minute
    presence_share  REAL NOT NULL,         -- 0.0-1.0 Anteil presence>=1
    movement_share  REAL NOT NULL,         -- 0.0-1.0 Anteil presence=2
    distance_avg    REAL,                  -- cm (NULL wenn keine Distanz-Samples)
    distance_min    INTEGER,
    distance_max    INTEGER,
    synced          INTEGER DEFAULT 0,
    UNIQUE(session_id, ts_minute)
);

CREATE INDEX IF NOT EXISTS idx_radar_minute_session
    ON sensor_radar_minute(session_id, ts_minute);
CREATE INDEX IF NOT EXISTS idx_radar_minute_synced
    ON sensor_radar_minute(synced);
