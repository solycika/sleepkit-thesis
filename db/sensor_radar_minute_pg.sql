-- ═══════════════════════════════════════════════════════════
-- SleepKit – mmWave Minuten-Aggregat (PostgreSQL, EC2)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sensor_radar_minute (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    ts_minute       TIMESTAMPTZ NOT NULL,
    n_samples       INTEGER NOT NULL,
    presence_share  NUMERIC(4,3) NOT NULL,
    movement_share  NUMERIC(4,3) NOT NULL,
    distance_avg    NUMERIC(7,1),
    distance_min    INTEGER,
    distance_max    INTEGER,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, ts_minute)
);

CREATE INDEX IF NOT EXISTS idx_radar_minute_session_ts
    ON sensor_radar_minute(session_id, ts_minute);
