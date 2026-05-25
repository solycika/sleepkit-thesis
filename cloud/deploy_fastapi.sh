#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# SleepKit FastAPI Backend – Deployment-Skript (EC2-Server)
# Ausführen als root: sudo bash deploy_fastapi.sh
#
# Voraussetzungen (sollten alle schon da sein):
#   - PostgreSQL läuft mit DB sleepkit_db, User sleepkit
#   - Caddy läuft mit HTTPS-Zertifikat für deine Domain
#     (mit handle /api/* + handle { try_files + file_server }
#      in mutually-exclusive handle-Blöcken)
#   - Linux-User "sleepkit" existiert (vom Bridge-Setup)
#   - Cognito-User-Pool sleepkit-users mit admin01 + p01
#
# Stand: Mai 2026 – v1.2
#   - Pydantic-Diary-Models 1:1 zum sleepkit_pg_schema.sql
#     (29/21/14 Felder, ENUMs als Literal, CHECK-Constraints
#      als Field(ge=,le=))
#   - JWT-Validierung: at_hash-Check für ID-Tokens deaktiviert
#     (python-jose würde sonst das Access-Token verlangen)
#   - Cognito app_client_id auf produktiven Wert gesetzt
#     (DEINE_COGNITO_APP_CLIENT_ID – identisch zum Frontend)
#   - Endpoint GET /api/users/{user_id}/profile für gender-Logik
# ═══════════════════════════════════════════════════════════

set -euo pipefail

API_DIR="/opt/sleepkit-api"
API_USER="sleepkit"
SRC_DIR="${API_DIR}/src"
CFG_FILE="${API_DIR}/api_config.yaml"
VENV="${API_DIR}/venv"
SVC_FILE="/etc/systemd/system/sleepkit-api.service"

# ── 0. Root-Check ─────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "✘ Bitte mit sudo ausführen: sudo bash deploy_fastapi.sh"
  exit 1
fi

echo "═══ SleepKit FastAPI Deployment (v1.2) ═══"
echo ""

# ── 1. Linux-User & Verzeichnisse ─────────────────────────
echo "──▶ [1/6] Verzeichnisse anlegen..."
id -u "${API_USER}" &>/dev/null || useradd -r -s /bin/false "${API_USER}"
mkdir -p "${SRC_DIR}" "${API_DIR}/logs"
chown -R "${API_USER}:${API_USER}" "${API_DIR}"
echo "  ✔ ${API_DIR} bereit"

# ── 2. Python venv + Pakete ───────────────────────────────
echo "──▶ [2/6] Python-Pakete installieren..."
if [[ ! -d "${VENV}" ]]; then
  apt install -y python3-venv python3-pip >/dev/null 2>&1 || true
  sudo -u "${API_USER}" python3 -m venv "${VENV}"
  echo "  ✔ venv neu erstellt"
else
  echo "  ✔ venv existiert (von Bridge-Setup)"
fi

sudo -u "${API_USER}" "${VENV}/bin/pip" install --quiet --upgrade pip
sudo -u "${API_USER}" "${VENV}/bin/pip" install --quiet \
  fastapi 'uvicorn[standard]' 'python-jose[cryptography]' \
  psycopg2-binary pyyaml requests
echo "  ✔ Pakete installiert"

# ── 3. main.py schreiben ──────────────────────────────────
echo "──▶ [3/6] main.py schreiben..."
cat > "${SRC_DIR}/main.py" << 'PYEOF'
#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════
SleepKit FastAPI Backend – v1.2
═══════════════════════════════════════════════════════════
- JWT-Validierung gegen AWS Cognito (über JWKS)
- REST-Endpoints für Schlaftagebuch, Sessions, Sensordaten,
  Admin-Reports
"""

import logging
import os
from datetime import date, datetime, time
from functools import lru_cache
from typing import Literal, Optional

import psycopg2
import psycopg2.extras
import requests
import yaml
from fastapi import Depends, FastAPI, HTTPException, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from pydantic import BaseModel, ConfigDict, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sleepkit-api")

CONFIG_FILE = os.environ.get(
    "SLEEPKIT_API_CONFIG", "/opt/sleepkit-api/api_config.yaml"
)
with open(CONFIG_FILE) as f:
    CFG = yaml.safe_load(f)

COG = CFG["cognito"]
DB = CFG["database"]
ISSUER = (
    f"https://cognito-idp.{COG['region']}.amazonaws.com/"
    f"{COG['user_pool_id']}"
)
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"

ALLOWED_SENSOR_TABLES = {
    "sensor_bme688", "sensor_bh1750", "sensor_co2",
    "sensor_dust", "sensor_radar", "sensor_audio",
}

def get_db():
    conn = psycopg2.connect(
        host=DB["host"], port=DB["port"], dbname=DB["name"],
        user=DB["user"], password=DB["password"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        yield conn
    finally:
        conn.close()

@lru_cache(maxsize=1)
def get_jwks() -> dict:
    r = requests.get(JWKS_URL, timeout=5)
    r.raise_for_status()
    return r.json()

bearer = HTTPBearer()

def verify_token(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    """Prüft Cognito-JWT, gibt Claims zurück."""
    token = creds.credentials
    try:
        headers = jwt.get_unverified_header(token)
        kid = headers["kid"]
        key = next(
            (k for k in get_jwks()["keys"] if k["kid"] == kid),
            None,
        )
        if key is None:
            raise HTTPException(401, "Unbekannter Signing Key")

        unverified = jwt.get_unverified_claims(token)
        token_use = unverified.get("token_use")

        if token_use == "id":
            # at_hash-Check braucht das Access-Token, das wir
            # nicht haben → deaktivieren. Signatur/iss/aud reichen.
            claims = jwt.decode(
                token, key, algorithms=["RS256"],
                audience=COG["app_client_id"],
                issuer=ISSUER,
                options={"verify_at_hash": False},
            )
        elif token_use == "access":
            claims = jwt.decode(
                token, key, algorithms=["RS256"],
                issuer=ISSUER,
                options={"verify_aud": False},
            )
            if claims.get("client_id") != COG["app_client_id"]:
                raise HTTPException(401, "Falsche client_id")
        else:
            raise HTTPException(401, "Ungültiger token_use")

        return claims
    except jwt.JWTError as e:
        raise HTTPException(401, f"Ungültiges Token: {e}")

def get_current_user(claims: dict = Depends(verify_token)) -> dict:
    groups = claims.get("cognito:groups", [])
    username = (
        claims.get("cognito:username")
        or claims.get("username")
        or claims.get("sub")
    )
    return {
        "username": username,
        "email": claims.get("email"),
        "is_admin": "admin" in groups,
        "groups": groups,
        "sub": claims["sub"],
    }

def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Admin erforderlich")
    return user

# ── Pydantic-Models 1:1 zum sleepkit_pg_schema.sql ────────
MealSizeT     = Literal["leicht", "mittel", "schwer"]
RoomTempT     = Literal["zu_kalt", "angenehm", "zu_warm"]
SleepPosT     = Literal["ruecken", "seite", "bauch", "wechselnd"]
CyclePhaseT   = Literal["follikel", "ovulation", "luteal",
                        "menstruation", "na"]
DreamValT     = Literal["positiv", "neutral", "negativ", "gemischt"]
PlatformT     = Literal["apple", "android"]


class DiaryEvening(BaseModel):
    """Abendprotokoll – vor dem Lichtlöschen."""
    model_config = ConfigDict(extra="forbid")

    user_id:    str
    diary_date: date

    leistungsfaehigkeit:       Optional[int]  = Field(default=None, ge=1, le=6)
    erschoepfung_tags:         Optional[int]  = Field(default=None, ge=0, le=3)
    nap_dauer_min:             Optional[int]  = None
    nap_uhrzeit_von:           Optional[time] = None
    nap_uhrzeit_bis:           Optional[time] = None
    alkohol_was:               Optional[str]  = None
    alkohol_menge:             Optional[str]  = None
    zubett_uhrzeit:            Optional[time] = None
    stimmung_abend:            Optional[int]  = Field(default=None, ge=1, le=6)

    koffein_anzahl:            Optional[int]  = None
    koffein_letzte_uhrzeit:    Optional[time] = None
    bewegung_art:              Optional[str]  = None
    bewegung_dauer_min:        Optional[int]  = None
    bewegung_uhrzeit:          Optional[time] = None
    stresslevel:               Optional[int]  = Field(default=None, ge=1, le=5)
    bildschirmzeit:            Optional[bool] = None
    bildschirmzeit_geraet:     Optional[str]  = None
    besondere_ereignisse:      Optional[str]  = None
    abendmahlzeit_uhrzeit:     Optional[time] = None
    abendmahlzeit_groesse:     Optional[MealSizeT] = None
    raumtemp_subjektiv:        Optional[RoomTempT] = None

    zyklus_phase:              Optional[CyclePhaseT] = None
    menstruation_beschwerden:  Optional[int]  = Field(default=None, ge=0, le=3)
    schwangerschaftswoche:     Optional[int]  = None
    hitzewallungen_anzahl:     Optional[int]  = None
    hormonelle_verhuetung:     Optional[bool] = None
    hormonelle_verhuetung_art: Optional[str]  = None

    nykturie_toilettengaenge:  Optional[int]  = None
    energieniveau_subj:        Optional[int]  = Field(default=None, ge=1, le=5)


class DiaryMorning(BaseModel):
    """Morgenprotokoll – nach dem Aufstehen."""
    model_config = ConfigDict(extra="forbid")

    user_id:    str
    diary_date: date

    stimmung_morgen:           Optional[int]  = Field(default=None, ge=1, le=6)
    erholsam:                  Optional[int]  = Field(default=None, ge=1, le=5)
    einschlaflatenz_min:       Optional[int]  = None
    naechtlich_wach_anzahl:    Optional[int]  = None
    naechtlich_wach_dauer_min: Optional[int]  = None
    aufgewacht_uhrzeit:        Optional[time] = None
    geschlafen_h:              Optional[int]  = None
    geschlafen_min:            Optional[int]  = None
    aufgestanden_uhrzeit:      Optional[time] = None
    schlafmedikament:          Optional[str]  = None
    schlafmedikament_dosis:    Optional[str]  = None
    schlafmedikament_uhrzeit:  Optional[time] = None

    schlafposition:            Optional[SleepPosT] = None
    traum_erinnert:            Optional[bool] = None
    traum_valenz:              Optional[DreamValT] = None
    schmerzen_aufwachen:       Optional[bool] = None
    schmerzen_lokation:        Optional[str]  = None
    schmerzen_intensitaet:     Optional[int]  = Field(default=None, ge=0, le=10)
    toilettengaenge_anzahl:    Optional[int]  = None
    schnarchen_selbst:         Optional[int]  = Field(default=None, ge=0, le=3)
    schnarchen_partner:        Optional[str]  = None


class DiarySmartwatch(BaseModel):
    """Smartwatch-Daten – morgens manuell eingetragen."""
    model_config = ConfigDict(extra="forbid")

    user_id:    str
    diary_date: date
    platform:   Optional[PlatformT] = None

    gesamtschlaf_min:    Optional[int]   = None
    tiefschlaf_min:      Optional[int]   = None
    leichtschlaf_min:    Optional[int]   = None
    rem_min:             Optional[int]   = None
    wach_min:            Optional[int]   = None
    herzfrequenz_avg:    Optional[int]   = None
    herzfrequenz_min:    Optional[int]   = None
    herzfrequenz_max:    Optional[int]   = None
    hrv:                 Optional[float] = None
    spo2_avg:            Optional[float] = None
    spo2_min:            Optional[float] = None
    schritte:            Optional[int]   = None
    atemfrequenz_avg:    Optional[float] = None


# ── App ───────────────────────────────────────────────────
app = FastAPI(title="SleepKit API", version="1.2")

@app.on_event("startup")
def startup():
    log.info("SleepKit API gestartet")
    log.info("Cognito Issuer: %s", ISSUER)
    log.info("DB: %s@%s/%s", DB["user"], DB["host"], DB["name"])

@app.get("/api/health")
def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}

@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    return user

@app.get("/api/sessions/{user_id}")
def list_sessions(
    user_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    if not user["is_admin"] and user["username"] != user_id:
        raise HTTPException(403, "Nicht erlaubt")
    with db.cursor() as cur:
        cur.execute(
            "SELECT session_id, user_id, user_name, started_at, ended_at "
            "FROM sessions WHERE user_id = %s "
            "ORDER BY started_at DESC LIMIT 200",
            (user_id,),
        )
        return cur.fetchall()

@app.get("/api/sensor/{table}/{session_id}")
def get_sensor_data(
    table: str = Path(..., description="z.B. sensor_bme688"),
    session_id: str = Path(...),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    if table not in ALLOWED_SENSOR_TABLES:
        raise HTTPException(400, "Tabelle nicht erlaubt")
    with db.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM sessions WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Session nicht gefunden")
        if not user["is_admin"] and row["user_id"] != user["username"]:
            raise HTTPException(403, "Nicht erlaubt")
        cur.execute(
            f"SELECT * FROM {table} WHERE session_id = %s "
            f"ORDER BY ts ASC LIMIT 50000",
            (session_id,),
        )
        return cur.fetchall()

# ── Schlaftagebuch ────────────────────────────────────────
def _upsert_diary(db, table: str, data: BaseModel) -> None:
    """Generischer UPSERT für alle drei Diary-Tabellen."""
    payload = data.model_dump(exclude_unset=False)
    cols = list(payload.keys())
    vals = list(payload.values())
    placeholders = ",".join(["%s"] * len(cols))
    col_str = ",".join(cols)
    update_str = ",".join(
        f"{c}=EXCLUDED.{c}" for c in cols
        if c not in ("user_id", "diary_date")
    )
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) "
            f"ON CONFLICT (user_id, diary_date) "
            f"DO UPDATE SET {update_str}",
            vals,
        )
        db.commit()


@app.post("/api/diary/evening", status_code=201)
def save_evening(
    data: DiaryEvening,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    if not user["is_admin"] and user["username"] != data.user_id:
        raise HTTPException(403, "Nicht erlaubt")
    _upsert_diary(db, "diary_evening", data)
    return {"status": "ok", "table": "diary_evening",
            "user_id": data.user_id, "diary_date": str(data.diary_date)}


@app.post("/api/diary/morning", status_code=201)
def save_morning(
    data: DiaryMorning,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    if not user["is_admin"] and user["username"] != data.user_id:
        raise HTTPException(403, "Nicht erlaubt")
    _upsert_diary(db, "diary_morning", data)
    return {"status": "ok", "table": "diary_morning",
            "user_id": data.user_id, "diary_date": str(data.diary_date)}


@app.post("/api/diary/smartwatch", status_code=201)
def save_smartwatch(
    data: DiarySmartwatch,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    if not user["is_admin"] and user["username"] != data.user_id:
        raise HTTPException(403, "Nicht erlaubt")
    _upsert_diary(db, "diary_smartwatch", data)
    return {"status": "ok", "table": "diary_smartwatch",
            "user_id": data.user_id, "diary_date": str(data.diary_date)}


@app.get("/api/diary/{user_id}/{diary_date}")
def get_diary(
    user_id: str,
    diary_date: date,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    if not user["is_admin"] and user["username"] != user_id:
        raise HTTPException(403, "Nicht erlaubt")
    out = {}
    with db.cursor() as cur:
        for tbl in ("diary_evening", "diary_morning", "diary_smartwatch"):
            cur.execute(
                f"SELECT * FROM {tbl} "
                f"WHERE user_id=%s AND diary_date=%s",
                (user_id, diary_date),
            )
            out[tbl] = cur.fetchone()
    return out


@app.get("/api/users/{user_id}/profile")
def get_user_profile(
    user_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Profildaten eines Users (inkl. gender für Diary-Form-Logik)."""
    if not user["is_admin"] and user["username"] != user_id:
        raise HTTPException(403, "Nicht erlaubt")
    with db.cursor() as cur:
        cur.execute(
            "SELECT user_id, role, user_name, email, age, gender, "
            "height_cm, weight_kg, medications, chronic_issues "
            "FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "User nicht gefunden")
        return row


# ── Admin ─────────────────────────────────────────────────
@app.get("/api/admin/users")
def list_users(
    user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    with db.cursor() as cur:
        cur.execute(
            "SELECT user_id, user_name, email, role, created_at "
            "FROM users ORDER BY user_id"
        )
        return cur.fetchall()

@app.get("/api/admin/report/{user_id}")
def admin_report(
    user_id: str,
    user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Roh-Daten für Report-Generator."""
    with db.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
        u = cur.fetchone()
        if not u:
            raise HTTPException(404, "User nicht gefunden")
        cur.execute(
            "SELECT * FROM sessions WHERE user_id=%s "
            "ORDER BY started_at",
            (user_id,),
        )
        sessions = cur.fetchall()
        cur.execute(
            "SELECT * FROM diary_evening WHERE user_id=%s "
            "ORDER BY diary_date",
            (user_id,),
        )
        evenings = cur.fetchall()
        cur.execute(
            "SELECT * FROM diary_morning WHERE user_id=%s "
            "ORDER BY diary_date",
            (user_id,),
        )
        mornings = cur.fetchall()
        cur.execute(
            "SELECT * FROM diary_smartwatch WHERE user_id=%s "
            "ORDER BY diary_date",
            (user_id,),
        )
        watches = cur.fetchall()
    return {
        "user": u,
        "sessions": sessions,
        "diary_evening": evenings,
        "diary_morning": mornings,
        "diary_smartwatch": watches,
    }
PYEOF

chown "${API_USER}:${API_USER}" "${SRC_DIR}/main.py"
echo "  ✔ ${SRC_DIR}/main.py"

# ── 4. Konfigurationsdatei (Template) ─────────────────────
echo "──▶ [4/6] Konfiguration..."
if [[ ! -f "${CFG_FILE}" ]]; then
  cat > "${CFG_FILE}" << 'CFGEOF'
# ═══════════════════════════════════════════════════════════
# SleepKit FastAPI Konfiguration
# WICHTIG: DB-Passwort unten eintragen!
# ═══════════════════════════════════════════════════════════
database:
  host: localhost
  port: 5432
  name: sleepkit_db
  user: sleepkit
  password: "DEIN_DB_PASSWORT_HIER"

cognito:
  region: eu-central-1
  user_pool_id: eu-central-1_DEINE_POOL_ID
  app_client_id: DEINE_COGNITO_APP_CLIENT_ID
CFGEOF
  chown "${API_USER}:${API_USER}" "${CFG_FILE}"
  chmod 600 "${CFG_FILE}"
  echo "  ✔ Template erstellt: ${CFG_FILE}"
  echo "  ⚠ DB-Passwort eintragen — siehe nächste Schritte unten!"
else
  echo "  ✔ ${CFG_FILE} existiert (nicht überschrieben)"
fi

# ── 5. systemd-Service ────────────────────────────────────
echo "──▶ [5/6] systemd-Service..."
cat > "${SVC_FILE}" << 'SVCEOF'
[Unit]
Description=SleepKit FastAPI Backend
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=sleepkit
Group=sleepkit
WorkingDirectory=/opt/sleepkit-api
Environment=PYTHONUNBUFFERED=1
Environment=SLEEPKIT_API_CONFIG=/opt/sleepkit-api/api_config.yaml
ExecStart=/opt/sleepkit-api/venv/bin/uvicorn src.main:app \
  --host 127.0.0.1 --port 8000 --workers 2
Restart=on-failure
RestartSec=10
TimeoutStopSec=10

NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/sleepkit-api/logs
ProtectHome=true

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable sleepkit-api.service >/dev/null 2>&1
echo "  ✔ ${SVC_FILE} installiert + enabled"

# ── 6. Abschluss ──────────────────────────────────────────
echo "──▶ [6/6] Fertig!"
echo ""
echo "═════════════════════════════════════════════════════════"
echo "  ✔ FastAPI installiert (noch NICHT gestartet)"
echo "═════════════════════════════════════════════════════════"
echo ""
echo "  NÄCHSTE SCHRITTE:"
echo ""
echo "  1) DB-Passwort in der Config eintragen:"
echo "     sudo nano ${CFG_FILE}"
echo ""
echo "  2) Service starten:"
echo "     sudo systemctl start sleepkit-api"
echo ""
echo "  3) Status prüfen:"
echo "     sudo systemctl status sleepkit-api"
echo "     journalctl -u sleepkit-api -f   (Strg-C zum Beenden)"
echo ""
echo "  4) Lokal testen (ohne Auth):"
echo "     curl http://127.0.0.1:8000/api/health"
echo "     → sollte JSON {\"status\":\"ok\",...} liefern"
echo ""
echo "  5) Auth-Test (sollte 401 / 403 liefern):"
echo "     curl http://127.0.0.1:8000/api/me"
echo "     → 401 = Auth korrekt verkabelt"
echo ""
echo "  6) Caddyfile prüfen — handle /api/* + handle { try_files +"
echo "     file_server } müssen in mutually-exclusive Blöcken stehen,"
echo "     sonst rewriter try_files den /api/*-Pfad zur index.html."
echo "       sudo cat /etc/caddy/Caddyfile"
echo "       sudo systemctl reload caddy"
echo ""
echo "═════════════════════════════════════════════════════════"
