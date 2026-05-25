# SleepKit

Sleep environment monitoring system — entwickelt im Rahmen einer Masterarbeit.

SleepKit erfasst über einen Raspberry Pi 5 (Edge Node) sechs Umgebungssensoren
in einem Schlafraum, puffert die Daten lokal in SQLite und überträgt sie per
MQTT/TLS an ein AWS-Backend (EC2). Dort landen sie in PostgreSQL und werden
über eine FastAPI-Schnittstelle sowie ein Web-Portal ausgewertet.

> **Hinweis zum Studienkontext:** Das System wurde für eine kleine Studie mit
> einem Teilnehmer (`p01`) und einem Administrator (`admin01`) konzipiert. Es
> werden ausschließlich anonyme, generische IDs verwendet. Dieses Repository
> enthält **nur Code und Datenbankstruktur**, keinerlei Studien- oder
> Teilnehmerdaten.

---

## Architektur

```
┌─────────────────────────┐         MQTT / TLS         ┌──────────────────────────┐
│   Raspberry Pi 5         │  ───────────────────────▶  │   AWS EC2 (eu-central-1)  │
│   (Edge Node)            │       Port 8883            │                           │
│                          │                            │   Mosquitto (Broker)      │
│   collector.py           │                            │   mqtt_bridge.py          │
│   ├─ 6 Sensoren          │                            │   PostgreSQL              │
│   ├─ SQLite-Puffer       │                            │   FastAPI (main.py)       │
│   └─ sync_mqtt.py        │                            │   Caddy (HTTPS)           │
└─────────────────────────┘                            │   Cognito (Auth)          │
                                                        └──────────────────────────┘
                                                                     │
                                                                     ▼
                                                          Web-Portal (index.html)
```

### Sensoren

| Sensor      | Messgröße                                  | Anschluss          |
|-------------|--------------------------------------------|--------------------|
| BME688      | Temperatur, Feuchte, Druck, Gas (VOC)      | SPI                |
| BH1750      | Helligkeit (Lux)                           | I²C                |
| MH-Z19C     | CO₂                                        | UART               |
| PMS5003     | Feinstaub (PM1.0 / PM2.5 / PM10)           | UART               |
| S3KM1110    | mmWave-Radar: Präsenz & Bewegung           | UART               |
| 2× INMP441  | Geräuschpegel / Schnarchen (FFT)           | I²S                |
| DS3231 RTC  | Batteriegepufferte Offline-Zeitstempel     | I²C                |

---

## Verzeichnisstruktur

```
.
├── pi/          Raspberry-Pi-Seite (Edge Node)
├── cloud/       AWS-EC2-Seite (Backend)
├── frontend/    Web-Portal
└── db/          Datenbank-Schema & Migrationen
```

### `pi/` — Raspberry Pi 5

| Datei                          | Zweck                                                  |
|--------------------------------|--------------------------------------------------------|
| `collector.py`                 | Sensor-Daemon (sechs Threads), schreibt nach SQLite    |
| `sync_mqtt.py`                 | Überträgt gepufferte Daten per MQTT/TLS (QoS 1)        |
| `sleepkit_schema.sql`          | SQLite-Schema (lokaler Offline-Puffer)                 |
| `setup_sleepkit.sh`            | Komplettes Pi-Setup (System, Pakete, Verzeichnisse)    |
| `deploy_collector.sh`          | Installiert Collector + Sync als systemd-Services      |
| `cleanup_radar.sh`             | Cron-Job: löscht Radar-Rohdaten älter als 30 Tage      |
| `sleepkit-collector.service`   | systemd-Unit für den Collector                         |
| `sleepkit-mqtt-sync.service`   | systemd-Unit für den MQTT-Sync                         |

### `cloud/` — AWS EC2

| Datei                          | Zweck                                                  |
|--------------------------------|--------------------------------------------------------|
| `main.py`                      | FastAPI-Backend (Auth via Cognito, REST-Endpoints)     |
| `mqtt_bridge.py`               | Subscribt MQTT-Topics, schreibt nach PostgreSQL        |
| `deploy_fastapi.sh`            | Deployment des FastAPI-Backends                        |
| `deploy_admin.sh`              | Spielt die Admin-Endpoints ein                         |
| `sleepkit-mqtt-bridge.service` | systemd-Unit für die MQTT-Bridge                       |

### `db/` — Datenbank

| Datei                          | Zweck                                                  |
|--------------------------------|--------------------------------------------------------|
| `sleepkit_pg_schema.sql`       | PostgreSQL-Schema (Live-Stand, 12 Tabellen, nur Struktur) |
| `sensor_radar_minute_pg.sql`   | Tabelle für Radar-Minuten-Aggregate (PostgreSQL)       |
| `sensor_radar_minute_sqlite.sql` | dieselbe Tabelle für SQLite (Pi-Seite)               |
| `migrate_radar.sh`             | Einmalige mmWave-Migration (Roh → Minuten-Aggregat)    |
| `migrate_apnoe.sql`            | Migration: Spalte `apnoe_risiko` in `diary_smartwatch` |

### `frontend/`

| Datei         | Zweck                                                          |
|---------------|----------------------------------------------------------------|
| `index.html`  | Web-Portal (Single-File): Login via Cognito, Report-Auslösung  |

---

## Konfiguration & Secrets

Dieses Repository ist öffentlich. Alle Zugangsdaten und umgebungsspezifischen
Werte wurden durch **Platzhalter** ersetzt. Vor dem Betrieb müssen folgende
Werte gesetzt werden:

| Platzhalter                    | Bedeutung                              | Wo                                      |
|--------------------------------|----------------------------------------|-----------------------------------------|
| `DEINE_COGNITO_APP_CLIENT_ID`  | Cognito App-Client-ID                  | `frontend/index.html`, `cloud/deploy_fastapi.sh` |
| `eu-central-1_DEINE_POOL_ID`   | Cognito User-Pool-ID                   | `cloud/deploy_fastapi.sh`               |
| `https://DEINE-DOMAIN.example/`| Eigene Domain / Redirect-URI           | `frontend/index.html`, `cloud/deploy_admin.sh` |
| `DEIN_DB_PASSWORT_HIER`        | PostgreSQL-Passwort                    | `cloud/deploy_fastapi.sh` (config.yaml) |
| `HIER_PASSWORT_SETZEN`         | MQTT-Passwort (Mosquitto)              | `pi/setup_sleepkit.sh`, `pi/deploy_collector.sh` |

Die echten Werte gehören in eine `config.yaml` bzw. Umgebungsvariablen, die
**nicht** eingecheckt werden (siehe `.gitignore`).

---

## Komponenten-Stack

- **Edge:** Raspberry Pi 5, Python, SQLite, paho-mqtt
- **Transport:** MQTT über TLS (Port 8883), QoS 1
- **Cloud:** AWS EC2 (eu-central-1), Mosquitto, PostgreSQL 18, FastAPI, Caddy (Let's Encrypt), AWS Cognito
- **Frontend:** statisches HTML/JS, Cognito Hosted UI (Authorization Code Grant)

---

## Lizenz

Der eigene Quellcode dieses Projekts steht unter der **MIT-Lizenz** (siehe `LICENSE`).

Verwendete Drittanbieter-Bibliotheken stehen unter eigenen Lizenzen — eine
Übersicht findet sich in `THIRD_PARTY_LICENSES.md`. Insbesondere: Der BME688
wird über die Adafruit-Bibliothek (MIT) ausgelesen; die proprietären
**Bosch-BSEC**-Algorithmen werden **nicht** verwendet und sind nicht Teil dieses
Repositories.

© 2026 Monika Fuezy-Somosi BSc.
