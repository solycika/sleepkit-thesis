-- ═══════════════════════════════════════════════════════════
-- SleepKit – Migration: Schlafapnoe-Risiko Spalte
-- ═══════════════════════════════════════════════════════════
-- Fügt eine Freitext-Spalte für die Schlafapnoe-/Atemstörungs-
-- Anzeige der Smartwatch-App hinzu (Apple Watch ab watchOS 11,
-- Samsung Galaxy Watch, Fitbit etc.).
--
-- Anwenden:
--   sudo -u postgres psql sleepkit_db < /tmp/migrate_apnoe.sql
-- ═══════════════════════════════════════════════════════════

ALTER TABLE diary_smartwatch
    ADD COLUMN IF NOT EXISTS apnoe_risiko TEXT;

-- Hinweis: TEXT statt VARCHAR(n), weil der angezeigte App-Text
-- in Zukunft länger werden könnte (z.B. mehrsprachige Hinweise).
-- Frontend limitiert auf 200 Zeichen via maxlength.
