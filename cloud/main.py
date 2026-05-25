#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════
SleepKit FastAPI Backend
═══════════════════════════════════════════════════════════
- JWT-Validierung gegen AWS Cognito (über JWKS)
- REST-Endpoints für Schlaftagebuch, Sessions, Sensordaten,
  Admin-Reports
- Sensordaten kommen NICHT hier rein (das macht die
  MQTT-Bridge), diese API liest sie nur aus PostgreSQL.

Konfiguration: /opt/sleepkit-api/api_config.yaml

Stand Mai 2026 – v1.2:
  - Pydantic-Diary-Models 1:1 zum sleepkit_pg_schema.sql
  - at_hash-Validierung für ID-Tokens deaktiviert
    (Cognito setzt at_hash, wir validieren signatur+aud+iss)
  - Endpoint GET /api/users/{user_id}/profile für gender-Logik
"""

import csv
import io
import logging
import os
import zipfile
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Literal, Optional

import psycopg2
import psycopg2.extras
import requests
import yaml
from fastapi import Depends, FastAPI, HTTPException, Path
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sleepkit-api")

# ── Konfiguration ─────────────────────────────────────────
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

# Whitelist erlaubter Sensor-Tabellen (gegen SQL-Injection)
ALLOWED_SENSOR_TABLES = {
    "sensor_bme688", "sensor_bh1750", "sensor_co2",
    "sensor_dust", "sensor_radar", "sensor_radar_minute", "sensor_audio",
}

# ── DB-Verbindung pro Request ─────────────────────────────
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

# ── Cognito JWT-Validierung ───────────────────────────────
@lru_cache(maxsize=1)
def get_jwks() -> dict:
    """JWKS einmal laden und cachen."""
    r = requests.get(JWKS_URL, timeout=5)
    r.raise_for_status()
    return r.json()

bearer = HTTPBearer()

def verify_token(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    """Prüft Cognito-JWT, gibt Claims zurück.

    Unterstützt sowohl ID-Tokens (Frontend-Login per OIDC) als
    auch Access-Tokens (z.B. M2M-Flows). Beide werden über
    Cognitos JWKS gegen Signatur+Issuer geprüft.
    """
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

        # Token-Use prüfen: ID-Token hat audience, Access-Token nicht
        unverified = jwt.get_unverified_claims(token)
        token_use = unverified.get("token_use")

        if token_use == "id":
            # ID-Token: audience-Check + at_hash-Check deaktivieren.
            # at_hash würde das Access-Token erfordern, das wir
            # hier nicht haben. Signatur + iss + aud genügen für
            # die Authentizitätsprüfung.
            claims = jwt.decode(
                token, key, algorithms=["RS256"],
                audience=COG["app_client_id"],
                issuer=ISSUER,
                options={"verify_at_hash": False},
            )
        elif token_use == "access":
            # Access-Token hat keine audience, dafür client_id
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
    """User-Info aus Claims extrahieren."""
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

# ═══════════════════════════════════════════════════════════
# Pydantic-Models für Schlaftagebuch
# ═══════════════════════════════════════════════════════════
# Felder und Constraints sind 1:1 abgeleitet aus
# sleepkit_pg_schema.sql. Reihenfolge entspricht dem Schema.
# Alle Felder außer user_id und diary_date sind optional.

# ENUM-Typen aus dem Schema (Literal = strikte String-Werte)
MealSizeT     = Literal["leicht", "mittel", "schwer"]
RoomTempT     = Literal["zu_kalt", "angenehm", "zu_warm"]
SleepPosT     = Literal["ruecken", "seite", "bauch", "wechselnd"]
CyclePhaseT   = Literal["follikel", "ovulation", "luteal",
                        "menstruation", "na"]
DreamValT     = Literal["positiv", "neutral", "negativ", "gemischt"]
PlatformT     = Literal["apple", "android"]


class DiaryEvening(BaseModel):
    """Abendprotokoll – vor dem Lichtlöschen.
    UNIQUE(user_id, diary_date) im Schema → UPSERT-fähig."""
    model_config = ConfigDict(extra="forbid")

    user_id:    str
    diary_date: date

    # ── DGSM-Originalfelder ───────────────────────────────
    leistungsfaehigkeit:      Optional[int] = Field(default=None, ge=1, le=6)
    erschoepfung_tags:        Optional[int] = Field(default=None, ge=0, le=3)
    nap_dauer_min:            Optional[int] = None
    nap_uhrzeit_von:          Optional[time] = None
    nap_uhrzeit_bis:          Optional[time] = None
    alkohol_was:              Optional[str] = None
    alkohol_menge:            Optional[str] = None
    zubett_uhrzeit:           Optional[time] = None
    stimmung_abend:           Optional[int] = Field(default=None, ge=1, le=6)

    # ── SleepKit-Erweiterungen ────────────────────────────
    koffein_anzahl:           Optional[int] = None
    koffein_letzte_uhrzeit:   Optional[time] = None
    bewegung_art:             Optional[str] = None
    bewegung_dauer_min:       Optional[int] = None
    bewegung_uhrzeit:         Optional[time] = None
    stresslevel:              Optional[int] = Field(default=None, ge=1, le=5)
    bildschirmzeit:           Optional[bool] = None
    bildschirmzeit_geraet:    Optional[str] = None
    besondere_ereignisse:     Optional[str] = None
    abendmahlzeit_uhrzeit:    Optional[time] = None
    abendmahlzeit_groesse:    Optional[MealSizeT] = None
    raumtemp_subjektiv:       Optional[RoomTempT] = None

    # ── Geschlechtsspezifisch (weiblich) ──────────────────
    zyklus_phase:             Optional[CyclePhaseT] = None
    menstruation_beschwerden: Optional[int] = Field(default=None, ge=0, le=3)
    schwangerschaftswoche:    Optional[int] = None
    hitzewallungen_anzahl:    Optional[int] = None
    hormonelle_verhuetung:    Optional[bool] = None
    hormonelle_verhuetung_art: Optional[str] = None

    # ── Geschlechtsspezifisch (männlich) ──────────────────
    nykturie_toilettengaenge: Optional[int] = None
    energieniveau_subj:       Optional[int] = Field(default=None, ge=1, le=5)


class DiaryMorning(BaseModel):
    """Morgenprotokoll – nach dem Aufstehen.
    UNIQUE(user_id, diary_date) im Schema → UPSERT-fähig."""
    model_config = ConfigDict(extra="forbid")

    user_id:    str
    diary_date: date

    # ── DGSM-Originalfelder ───────────────────────────────
    stimmung_morgen:           Optional[int] = Field(default=None, ge=1, le=6)
    erholsam:                  Optional[int] = Field(default=None, ge=1, le=5)
    einschlaflatenz_min:       Optional[int] = None
    naechtlich_wach_anzahl:    Optional[int] = None
    naechtlich_wach_dauer_min: Optional[int] = None
    aufgewacht_uhrzeit:        Optional[time] = None
    geschlafen_h:              Optional[int] = None
    geschlafen_min:            Optional[int] = None
    aufgestanden_uhrzeit:      Optional[time] = None
    schlafmedikament:          Optional[str] = None
    schlafmedikament_dosis:    Optional[str] = None
    schlafmedikament_uhrzeit:  Optional[time] = None

    # ── SleepKit-Erweiterungen ────────────────────────────
    schlafposition:            Optional[SleepPosT] = None
    traum_erinnert:            Optional[bool] = None
    traum_valenz:              Optional[DreamValT] = None
    schmerzen_aufwachen:       Optional[bool] = None
    schmerzen_lokation:        Optional[str] = None
    schmerzen_intensitaet:     Optional[int] = Field(default=None, ge=0, le=10)
    toilettengaenge_anzahl:    Optional[int] = None
    schnarchen_selbst:         Optional[int] = Field(default=None, ge=0, le=3)
    schnarchen_partner:        Optional[str] = None


class DiarySmartwatch(BaseModel):
    """Smartwatch-Daten – morgens manuell aus Apple Watch /
    Android Health-App eingetragen.
    UNIQUE(user_id, diary_date) → UPSERT-fähig."""
    model_config = ConfigDict(extra="forbid")

    user_id:    str
    diary_date: date
    platform:   Optional[PlatformT] = None

    gesamtschlaf_min:    Optional[int] = None
    tiefschlaf_min:      Optional[int] = None
    leichtschlaf_min:    Optional[int] = None
    rem_min:             Optional[int] = None
    wach_min:            Optional[int] = None
    herzfrequenz_avg:    Optional[int] = None
    herzfrequenz_min:    Optional[int] = None
    herzfrequenz_max:    Optional[int] = None
    hrv:                 Optional[float] = None
    spo2_avg:            Optional[float] = None
    spo2_min:            Optional[float] = None
    spo2_max:            Optional[float] = None
    schritte:            Optional[int] = None
    atemfrequenz_avg:    Optional[float] = None
    atemfrequenz_min:    Optional[float] = None
    atemfrequenz_max:    Optional[float] = None
    apnoe_risiko:        Optional[str] = None

    @model_validator(mode="after")
    def _autofill_averages(self):
        """Berechnet Durchschnittswerte aus Min+Max für die Vitalparameter,
        bei denen Apple Health nur Bereiche liefert (Atemfrequenz, SpO2,
        Herzfrequenz). Approximation: avg = (min + max) / 2.
        Wird NUR gemacht, wenn avg nicht explizit übermittelt wurde.
        HRV ist nicht dabei: Apple zeigt HRV als Tagesdurchschnitt direkt an.
        """
        pairs = [
            ("atemfrequenz_min", "atemfrequenz_max", "atemfrequenz_avg", 1),
            ("spo2_min",         "spo2_max",         "spo2_avg",         1),
            ("herzfrequenz_min", "herzfrequenz_max", "herzfrequenz_avg", 0),
        ]
        for min_f, max_f, avg_f, decimals in pairs:
            v_min = getattr(self, min_f)
            v_max = getattr(self, max_f)
            v_avg = getattr(self, avg_f)
            if v_avg is None and v_min is not None and v_max is not None:
                computed = round((float(v_min) + float(v_max)) / 2.0, decimals)
                # Herzfrequenz ist SMALLINT in der DB → int
                if decimals == 0:
                    computed = int(round(computed))
                setattr(self, avg_f, computed)
        return self


# ── App ───────────────────────────────────────────────────
app = FastAPI(title="SleepKit API", version="1.2")

@app.on_event("startup")
def startup():
    log.info("SleepKit API gestartet")
    log.info("Cognito Issuer: %s", ISSUER)
    log.info("DB: %s@%s/%s", DB["user"], DB["host"], DB["name"])

# ── Public ────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}

# ── Auth-Test ─────────────────────────────────────────────
@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    return user

# ── Sessions ──────────────────────────────────────────────
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

# ── Sensordaten ───────────────────────────────────────────
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

# ═══════════════════════════════════════════════════════════
# Schlaftagebuch
# ═══════════════════════════════════════════════════════════

def _upsert_diary(db, table: str, data: BaseModel) -> None:
    """Generischer UPSERT für alle drei Diary-Tabellen.
    Nutzt das ON CONFLICT (user_id, diary_date)-Pattern aus
    dem Schema. Keine SQL-Injection möglich, weil:
    - `table` kommt nur aus Endpoint-Konstanten
    - `cols` kommen aus Pydantic-Field-Namen (validiert)
    """
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


# ── User-Profil (für Frontend: gender → Felder einblenden) ─
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
            "height_cm, weight_kg, medications, chronic_issues, "
            "verhuetung_aktuell, verhuetung_art, verhuetung_seit "
            "FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "User nicht gefunden")
        return row


# ── Pydantic-Model für Profil-Update (PUT) ────────────────
class UserProfileUpdate(BaseModel):
    """Updatable Felder im User-Profil.
    user_id, role, cognito_sub, created_at, updated_at sind NICHT änderbar
    und werden hier bewusst nicht aufgeführt.
    Alle Felder sind optional — leeres Profil-Formular setzt überall NULL.
    """
    model_config = ConfigDict(extra="forbid")

    user_name:          Optional[str] = Field(default=None, max_length=200)
    email:              Optional[str] = Field(default=None, max_length=200)
    age:                Optional[int] = Field(default=None, ge=0, le=130)
    gender:             Optional[Literal["weiblich", "maennlich", "divers", "keine_angabe"]] = None
    height_cm:          Optional[int] = Field(default=None, ge=50, le=250)
    weight_kg:          Optional[float] = Field(default=None, ge=20, le=400)
    medications:        Optional[str] = Field(default=None, max_length=2000)
    chronic_issues:     Optional[str] = Field(default=None, max_length=2000)
    # Stammdatum: Verhütung (nur bei gender=weiblich relevant)
    verhuetung_aktuell: Optional[bool] = None
    verhuetung_art:     Optional[str]  = Field(default=None, max_length=200)
    verhuetung_seit:    Optional[date] = None


@app.put("/api/users/{user_id}/profile")
def update_user_profile(
    user_id: str,
    data: UserProfileUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Profildaten eines Users aktualisieren.
    user_name darf NICHT auf leer gesetzt werden (DB hat NOT NULL).
    Cognito-Sub, role, user_id bleiben unangetastet (kein Field im Model).
    """
    if not user["is_admin"] and user["username"] != user_id:
        raise HTTPException(403, "Nicht erlaubt")

    payload = data.model_dump(exclude_unset=False)

    # user_name muss vorhanden bleiben (NOT NULL in DB)
    if "user_name" in payload and payload["user_name"] in (None, ""):
        # Wenn explizit leer geschickt: behalte den alten Wert
        payload.pop("user_name", None)

    if not payload:
        raise HTTPException(400, "Kein Feld zum Aktualisieren")

    cols = list(payload.keys())
    vals = list(payload.values())
    set_str = ", ".join(f"{c} = %s" for c in cols)

    with db.cursor() as cur:
        cur.execute(
            f"UPDATE users SET {set_str}, updated_at = NOW() "
            f"WHERE user_id = %s "
            f"RETURNING user_id, role, user_name, email, age, gender, "
            f"  height_cm, weight_kg, medications, chronic_issues, "
            f"  verhuetung_aktuell, verhuetung_art, verhuetung_seit",
            vals + [user_id],
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "User nicht gefunden")
        db.commit()
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


# ═══════════════════════════════════════════════════════════
# Weekly Reports: Summary (JSON) + PDF + CSV-Zip
# ═══════════════════════════════════════════════════════════

WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _fetch_period_data(db, user_id: str, start_date: date,
                       end_date: date) -> dict:
    """Lädt alle Daten für einen frei gewählten Zeitraum.

    start_date und end_date sind beide inklusiv. Mindestens 1 Tag.
    Hard cap bei 92 Tagen, damit der PDF-Generator nicht 300 Seiten
    produziert. Das ist nur ein Sicherheitsnetz, kein methodisches Limit.
    """
    if start_date > end_date:
        raise HTTPException(400, "start_date muss <= end_date sein")
    n_days = (end_date - start_date).days + 1
    if n_days > 92:
        raise HTTPException(400, "Zeitraum maximal 92 Tage (3 Monate)")

    out = {"user": None, "start_date": start_date, "end_date": end_date,
           "n_days": n_days, "days": []}

    with db.cursor() as cur:
        # User-Profil (inkl. Verhütungs-Stammdaten)
        cur.execute(
            "SELECT user_id, role, user_name, email, age, gender, "
            "height_cm, weight_kg, medications, chronic_issues, "
            "verhuetung_aktuell, verhuetung_art, verhuetung_seit "
            "FROM users WHERE user_id=%s",
            (user_id,),
        )
        out["user"] = cur.fetchone()
        if not out["user"]:
            raise HTTPException(404, "User nicht gefunden")

        # Pro Tag im Fenster: Diary-Einträge + Sensor-Aggregate
        #
        # Konvention (DGSM-konform, Variante B):
        # Ein Tag-Eintrag d repräsentiert die NACHT, die am Morgen
        # des Tages d endet (Schlaf der Nacht (d-1) → d).
        #   diary_evening   wird mit dem Datum d-1 verknüpft
        #   diary_morning   bleibt bei d (Datum des Aufwachens)
        #   diary_smartwatch bleibt bei d (Apple Health-Konvention)
        #   Sensor-Nacht    = evening(d-1).zubett → morning(d).aufgestanden
        for i in range(n_days):
            d = start_date + timedelta(days=i)
            prev = d - timedelta(days=1)
            day_entry = {
                "date": d, "weekday": WEEKDAYS_DE[d.weekday()],
                "evening_date": prev,  # für die UI: welcher Abend gehört dazu
            }

            # Abendprotokoll vom VORTAG (Tag d-1)
            cur.execute(
                "SELECT * FROM diary_evening "
                "WHERE user_id=%s AND diary_date=%s",
                (user_id, prev),
            )
            day_entry["diary_evening"] = cur.fetchone()

            # Morgenprotokoll und Smartwatch vom heutigen Datum d
            for tbl in ("diary_morning", "diary_smartwatch"):
                cur.execute(
                    f"SELECT * FROM {tbl} WHERE user_id=%s AND diary_date=%s",
                    (user_id, d),
                )
                day_entry[tbl] = cur.fetchone()

            # Nacht-Fenster aus Tagebuch ableiten (Thesis Kap. 4.5.2):
            # zubett vom Abend(d-1) → aufgestanden vom Morgen(d).
            # Fallback wenn Zeiten fehlen: prev 22:00 → d 08:00.
            night_start, night_end, night_source = _derive_night_window(
                cur, user_id, d, day_entry["diary_evening"],
                day_entry["diary_morning"],
            )
            day_entry["night_start"] = night_start
            day_entry["night_end"] = night_end
            day_entry["night_source"] = night_source

            day_entry["sensors"] = _aggregate_sensors(
                cur, user_id, night_start, night_end
            )
            out["days"].append(day_entry)

    return out


def _derive_night_window(cur, user_id: str, d: date, evening, morning):
    """Bestimmt das Nacht-Fenster für Tag d (Variante B-Konvention).

    Die Nacht endet am Morgen des Tages d und begann am Abend von d-1.

    Vorrang (Thesis Kap. 4.5.2):
      1) evening(d-1).zubett_uhrzeit  →  morning(d).aufgestanden_uhrzeit
      2) evening(d-1).zubett_uhrzeit  →  morning(d).aufgewacht_uhrzeit
      3) Fallback: (d-1) 22:00 → d 08:00

    Args:
        evening: diary_evening Zeile vom Vortag (d-1)
        morning: diary_morning Zeile von Tag d
    Returns: (start_ts, end_ts, source_label)
    """
    prev = d - timedelta(days=1)

    zubett = evening.get("zubett_uhrzeit") if evening else None
    aufgestanden = morning.get("aufgestanden_uhrzeit") if morning else None
    aufgewacht = morning.get("aufgewacht_uhrzeit") if morning else None

    end_time = aufgestanden or aufgewacht
    if zubett and end_time:
        # zubett >= 15:00 → Tag prev; sonst (z.B. 01:30) → Tag d
        zubett_day = prev if zubett >= time(15, 0) else d
        start_ts = datetime.combine(zubett_day, zubett)
        end_ts = datetime.combine(d, end_time)
        if end_ts <= start_ts:
            end_ts = start_ts + timedelta(hours=10)
        label = "tagebuch" if aufgestanden else "tagebuch_aufgewacht"
        return start_ts, end_ts, label

    # Fallback
    return (
        datetime.combine(prev, time(22, 0)),
        datetime.combine(d, time(8, 0)),
        "fallback_2208",
    )


def _aggregate_sensors(cur, user_id: str, start_ts, end_ts) -> dict:
    """Liefert Aggregate (Min/Avg/Max + Count) je Sensortyp für ein Zeitfenster.
    Nutzt session-Zuordnung via user_id.
    """
    # Sessions des Users finden, die im Fenster aktiv waren
    cur.execute(
        "SELECT session_id FROM sessions WHERE user_id=%s "
        "AND (started_at <= %s) AND (ended_at IS NULL OR ended_at >= %s)",
        (user_id, end_ts, start_ts),
    )
    sess_rows = cur.fetchall()
    if not sess_rows:
        return {"has_data": False}
    sess_ids = tuple(r["session_id"] for r in sess_rows)

    agg = {"has_data": False}

    # BME688: Temp / Humidity / Pressure / Gas
    cur.execute(
        "SELECT "
        " AVG(temperature)::numeric(5,2)    AS temp_avg, "
        " MIN(temperature)::numeric(5,2)    AS temp_min, "
        " MAX(temperature)::numeric(5,2)    AS temp_max, "
        " AVG(humidity)::numeric(5,2)       AS hum_avg, "
        " AVG(pressure)::numeric(7,2)       AS press_avg, "
        " AVG(gas_resistance)::numeric(12,0) AS gas_avg, "
        " COUNT(*)                          AS n "
        "FROM sensor_bme688 "
        "WHERE session_id = ANY(%s) AND ts BETWEEN %s AND %s",
        (list(sess_ids), start_ts, end_ts),
    )
    r = cur.fetchone()
    if r and r.get("n", 0) > 0:
        agg["bme688"] = r
        agg["has_data"] = True

    # BH1750: Lux
    cur.execute(
        "SELECT AVG(lux)::numeric(8,1) AS lux_avg, "
        " MIN(lux)::numeric(8,1) AS lux_min, "
        " MAX(lux)::numeric(8,1) AS lux_max, "
        " COUNT(*) AS n "
        "FROM sensor_bh1750 "
        "WHERE session_id = ANY(%s) AND ts BETWEEN %s AND %s",
        (list(sess_ids), start_ts, end_ts),
    )
    r = cur.fetchone()
    if r and r.get("n", 0) > 0:
        agg["bh1750"] = r
        agg["has_data"] = True

    # CO2
    cur.execute(
        "SELECT AVG(co2_ppm)::numeric(7,1) AS co2_avg, "
        " MIN(co2_ppm) AS co2_min, MAX(co2_ppm) AS co2_max, "
        " COUNT(*) AS n "
        "FROM sensor_co2 "
        "WHERE session_id = ANY(%s) AND ts BETWEEN %s AND %s",
        (list(sess_ids), start_ts, end_ts),
    )
    r = cur.fetchone()
    if r and r.get("n", 0) > 0:
        agg["co2"] = r
        agg["has_data"] = True

    # Dust
    cur.execute(
        "SELECT AVG(pm1_0)::numeric(6,1) AS pm1_avg, "
        " AVG(pm2_5)::numeric(6,1) AS pm25_avg, "
        " AVG(pm10)::numeric(6,1)  AS pm10_avg, "
        " COUNT(*) AS n "
        "FROM sensor_dust "
        "WHERE session_id = ANY(%s) AND ts BETWEEN %s AND %s",
        (list(sess_ids), start_ts, end_ts),
    )
    r = cur.fetchone()
    if r and r.get("n", 0) > 0:
        agg["dust"] = r
        agg["has_data"] = True

    # Radar (Präsenz / Bewegung) – jetzt aus sensor_radar_minute
    # (Minuten-Aggregate, presence_share/movement_share als Anteile 0..1).
    # Rohdaten in sensor_radar bleiben lokal auf dem Pi (30 Tage Retention).
    try:
        cur.execute(
            "SELECT "
            " AVG(presence_share)::numeric(4,3) AS presence_share_avg, "
            " AVG(movement_share)::numeric(4,3) AS movement_share_avg, "
            " AVG(distance_avg)::numeric(7,1)   AS distance_avg, "
            " COUNT(*) AS n "
            "FROM sensor_radar_minute "
            "WHERE session_id = ANY(%s) AND ts_minute BETWEEN %s AND %s",
            (list(sess_ids), start_ts, end_ts),
        )
        r = cur.fetchone()
        if r and r.get("n", 0) > 0:
            agg["radar"] = r
            agg["has_data"] = True
    except psycopg2.Error:
        cur.connection.rollback()

    # Audio / Schnarchen (Schema-tolerant)
    try:
        cur.execute(
            "SELECT COUNT(*) AS n FROM sensor_audio "
            "WHERE session_id = ANY(%s) AND ts BETWEEN %s AND %s",
            (list(sess_ids), start_ts, end_ts),
        )
        r = cur.fetchone()
        if r and r.get("n", 0) > 0:
            agg["audio"] = r
            agg["has_data"] = True
    except psycopg2.Error:
        cur.connection.rollback()

    return agg


def _safe_avg(values):
    """Durchschnitt einer Liste, None-Werte ignorierend; gibt None bei leer."""
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def _build_summary(data: dict) -> dict:
    """Aggregiert Daten zu KPIs + Daily-Availability für die Vorschau."""
    days = data["days"]
    n = data["n_days"]

    # Tagesverfügbarkeit
    daily = []
    for d in days:
        daily.append({
            "date": d["date"].isoformat(),
            "weekday": d["weekday"],
            "evening": d["diary_evening"] is not None,
            "morning": d["diary_morning"] is not None,
            "smartwatch": d["diary_smartwatch"] is not None,
            "sensors": bool(d["sensors"].get("has_data")),
        })

    # KPI-Aggregate
    evening_days = sum(1 for d in days if d["diary_evening"])
    morning_days = sum(1 for d in days if d["diary_morning"])
    smartwatch_days = sum(1 for d in days if d["diary_smartwatch"])
    sensor_days = sum(1 for d in days if d["sensors"].get("has_data"))

    stimmungen = [d["diary_morning"]["stimmung_morgen"] for d in days
                  if d["diary_morning"] and d["diary_morning"].get("stimmung_morgen")]
    erholsam = [d["diary_morning"]["erholsam"] for d in days
                if d["diary_morning"] and d["diary_morning"].get("erholsam")]
    stress = [d["diary_evening"]["stresslevel"] for d in days
              if d["diary_evening"] and d["diary_evening"].get("stresslevel")]

    # Schlafdauer aus Diary (Std + Min)
    sleep_h_diary = []
    for d in days:
        m = d["diary_morning"]
        if m and m.get("geschlafen_h") is not None:
            total = (m.get("geschlafen_h") or 0) + (m.get("geschlafen_min") or 0) / 60.0
            sleep_h_diary.append(total)

    # Schlafdauer aus Smartwatch (Minuten → Stunden)
    sleep_h_sw = []
    for d in days:
        sw = d["diary_smartwatch"]
        if sw and sw.get("gesamtschlaf_min"):
            sleep_h_sw.append(sw["gesamtschlaf_min"] / 60.0)

    # Raumtemperatur (BME688)
    room_temps = []
    for d in days:
        bme = d["sensors"].get("bme688")
        if bme and bme.get("temp_avg") is not None:
            room_temps.append(float(bme["temp_avg"]))

    # Luftfeuchte (BME688)
    humidities = []
    for d in days:
        bme = d["sensors"].get("bme688")
        if bme and bme.get("hum_avg") is not None:
            humidities.append(float(bme["hum_avg"]))

    # CO2
    co2_avgs = []
    for d in days:
        co2 = d["sensors"].get("co2")
        if co2 and co2.get("co2_avg") is not None:
            co2_avgs.append(float(co2["co2_avg"]))

    # Feinstaub PM2.5 / PM10 (PMS5003)
    pm25_avgs = []
    pm10_avgs = []
    for d in days:
        dust = d["sensors"].get("dust")
        if dust:
            if dust.get("pm25_avg") is not None:
                pm25_avgs.append(float(dust["pm25_avg"]))
            if dust.get("pm10_avg") is not None:
                pm10_avgs.append(float(dust["pm10_avg"]))

    # Beleuchtung (BH1750)
    lux_avgs = []
    for d in days:
        lux = d["sensors"].get("bh1750")
        if lux and lux.get("lux_avg") is not None:
            lux_avgs.append(float(lux["lux_avg"]))

    # mmWave Präsenz / Bewegung (sensor_radar_minute)
    presence_shares = []
    movement_shares = []
    for d in days:
        radar = d["sensors"].get("radar")
        if radar:
            if radar.get("presence_share_avg") is not None:
                presence_shares.append(float(radar["presence_share_avg"]))
            if radar.get("movement_share_avg") is not None:
                movement_shares.append(float(radar["movement_share_avg"]))

    # Schnarch-Ereignisse: zähle nur Tage, an denen das Feld auch ausgefüllt
    # ist (None != 0). Liefert (events, documented) — sodass z.B. "0/2"
    # angezeigt werden kann, wenn an einem Tag das Feld leer blieb.
    snore_events = 0
    snore_documented = 0
    for d in days:
        m = d["diary_morning"]
        if not m:
            continue
        v = m.get("schnarchen_selbst")
        if v is None:
            continue
        snore_documented += 1
        if v >= 2:
            snore_events += 1

    kpis = {
        "n_days": n,
        "evening_days": evening_days,
        "morning_days": morning_days,
        "smartwatch_days": smartwatch_days,
        "sensor_days": sensor_days,
        "avg_stimmung_morgen": _safe_avg(stimmungen),
        "avg_erholsam": _safe_avg(erholsam),
        "avg_stress": _safe_avg(stress),
        "avg_sleep_h": _safe_avg(sleep_h_diary),
        "avg_sw_sleep_h": _safe_avg(sleep_h_sw),
        "avg_room_temp": _safe_avg(room_temps),
        "avg_humidity": _safe_avg(humidities),
        "avg_co2": _safe_avg(co2_avgs),
        "avg_pm2_5": _safe_avg(pm25_avgs),
        "avg_pm10": _safe_avg(pm10_avgs),
        "avg_lux": _safe_avg(lux_avgs),
        "avg_presence_share": _safe_avg(presence_shares),
        "avg_movement_share": _safe_avg(movement_shares),
        "snore_events": snore_events,
        "snore_documented": snore_documented,
    }

    return {
        "user_id": data["user"]["user_id"],
        "user_name": data["user"]["user_name"],
        "start_date": data["start_date"].isoformat(),
        "end_date": data["end_date"].isoformat(),
        "n_days": n,
        "daily_availability": daily,
        "kpis": kpis,
    }


# ── Formatter ──────────────────────────────────────────────
def _fmt(v, suffix=""):
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "Ja" if v else "Nein"
    if isinstance(v, float):
        return f"{v:.1f}{suffix}"
    return f"{v}{suffix}"


def _fmt_time(t):
    if t is None:
        return "—"
    if hasattr(t, "strftime"):
        return t.strftime("%H:%M")
    s = str(t)
    return s[:5] if len(s) >= 5 else s


def _register_pdf_fonts():
    """Registriert DejaVu Sans als Font-Familie für Unicode-Subscript-Support
    (CO₂, SpO₂). reportlab's Standard-Helvetica enthält keine Subscript-Glyphen.

    Wir registrieren die volle Familie (Regular + Oblique), damit Styles wie
    Italic intern korrekt aufgelöst werden (sonst crasht ps2tt() beim Parsen
    von <para>-Tags). Idempotent.

    Returns: (main_font_name, italic_font_name) zum Verwenden in ParagraphStyle
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping

    if "DejaVuSans" in pdfmetrics.getRegisteredFontNames():
        return "DejaVuSans", "DejaVuSans-Oblique"

    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Oblique.ttf"),
    ]
    for reg_path, italic_path in candidates:
        if os.path.exists(reg_path):
            pdfmetrics.registerFont(TTFont("DejaVuSans", reg_path))
            italic_name = "DejaVuSans"  # Default fallback
            if os.path.exists(italic_path):
                pdfmetrics.registerFont(TTFont("DejaVuSans-Oblique", italic_path))
                italic_name = "DejaVuSans-Oblique"
            # Familie registrieren: (Name, bold, italic) → konkrete Font
            # Das ist nötig, damit reportlab beim Parsen von <i>/<b> Tags
            # nicht crasht und ps2tt() die Varianten findet.
            addMapping("DejaVuSans", 0, 0, "DejaVuSans")
            addMapping("DejaVuSans", 1, 0, "DejaVuSans")   # bold → regular (fallback)
            addMapping("DejaVuSans", 0, 1, italic_name)
            addMapping("DejaVuSans", 1, 1, italic_name)
            return "DejaVuSans", italic_name

    # Fallback: Helvetica (Subscript wird nicht korrekt gerendert,
    # aber Generierung crasht nicht)
    return "Helvetica", "Helvetica-Oblique"


# ── PDF-Generierung ────────────────────────────────────────
def _build_pdf(data: dict) -> bytes:
    """Erzeugt das PDF-Dokument aus Wochen-Daten."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
        )
        from reportlab.lib.enums import TA_LEFT
    except ImportError:
        raise HTTPException(
            500,
            "reportlab nicht installiert. Bitte 'pip install reportlab' im "
            "API-venv ausführen und systemctl restart sleepkit-api."
        )

    main_font, italic_font = _register_pdf_fonts()
    summary = _build_summary(data)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"SleepKit Datenreport {data['user']['user_id']}",
        author="SleepKit Studie",
    )

    styles = getSampleStyleSheet()
    h_title = ParagraphStyle(
        "Title", parent=styles["Title"], fontSize=22, leading=26,
        fontName=main_font,
        alignment=TA_LEFT, textColor=colors.HexColor("#1a2240"), spaceAfter=4,
    )
    h_sub = ParagraphStyle(
        "Sub", parent=styles["Normal"], fontSize=11, leading=14,
        alignment=TA_LEFT, textColor=colors.HexColor("#9a8e76"),
        fontName=italic_font, spaceAfter=18,
    )
    h1 = ParagraphStyle(
        "H1", parent=styles["Heading1"], fontSize=15, leading=18,
        fontName=main_font,
        textColor=colors.HexColor("#1a2240"), spaceBefore=10, spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=12, leading=14,
        fontName=main_font,
        textColor=colors.HexColor("#d4a85f"), spaceBefore=8, spaceAfter=4,
    )
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=10, leading=13,
        fontName=main_font,
    )
    small = ParagraphStyle(
        "Small", parent=styles["Normal"], fontSize=8, leading=10,
        fontName=main_font,
        textColor=colors.HexColor("#5e5644"),
    )

    story = []
    u = data["user"]
    n = data["n_days"]

    # ─── Titelseite ─────────────────────────────────────────
    story.append(Paragraph("SleepKit Datenreport", h_title))
    story.append(Paragraph(
        f"{data['start_date'].strftime('%d.%m.%Y')} – "
        f"{data['end_date'].strftime('%d.%m.%Y')} "
        f"({n} {'Tag' if n == 1 else 'Tage'})", h_sub))

    profile_rows = [
        ["Teilnehmer:in", u["user_id"]],
        ["Name", u.get("user_name") or "—"],
        ["Alter", _fmt(u.get("age"))],
        ["Geschlecht", u.get("gender") or "—"],
        ["Größe", _fmt(u.get("height_cm"), " cm")],
        ["Gewicht", _fmt(float(u["weight_kg"]) if u.get("weight_kg") else None, " kg")],
        ["Medikamente", u.get("medications") or "—"],
        ["Chronische Erkrankungen", u.get("chronic_issues") or "—"],
    ]
    if u.get("gender") == "weiblich":
        profile_rows.append([
            "Verhütung",
            "—" if u.get("verhuetung_aktuell") is None
            else f"{'Ja' if u['verhuetung_aktuell'] else 'Nein'}"
            + (f" ({u['verhuetung_art']})" if u.get("verhuetung_art") else "")
        ])

    t = Table(profile_rows, colWidths=[6 * cm, 10 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), main_font),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5e5644")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e3dccb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # ─── KPI-Übersicht ──────────────────────────────────────
    story.append(Paragraph("Übersicht über den Berichtszeitraum", h1))
    k = summary["kpis"]
    kpi_rows = [
        ["Tagebuch Abend", f"{k['evening_days']}/{n} Tage"],
        ["Tagebuch Morgen", f"{k['morning_days']}/{n} Tage"],
        ["Smartwatch", f"{k['smartwatch_days']}/{n} Tage"],
        ["Sensoren", f"{k['sensor_days']}/{n} Nächte"],
        ["Ø Stimmung Morgen", _fmt(k['avg_stimmung_morgen'], " / 6")],
        ["Ø Erholsamkeit", _fmt(k['avg_erholsam'], " / 5")],
        ["Ø Stresslevel", _fmt(k['avg_stress'], " / 5")],
        ["Ø Schlafdauer (Diary)", _fmt(k['avg_sleep_h'], " h")],
        ["Ø Schlafdauer (Smartwatch)", _fmt(k['avg_sw_sleep_h'], " h")],
        ["Ø Raumtemperatur", _fmt(k['avg_room_temp'], " °C")],
        ["Ø Luftfeuchte", _fmt(k.get('avg_humidity'), " %")],
        ["Ø CO₂", _fmt(k['avg_co2'], " ppm")],
        ["Ø Feinstaub PM2.5", _fmt(k.get('avg_pm2_5'), " µg/m³")],
        ["Ø Feinstaub PM10", _fmt(k.get('avg_pm10'), " µg/m³")],
        ["Ø Beleuchtung", _fmt(k.get('avg_lux'), " lx")],
        ["Ø Präsenz (Radar)",
         f"{round(k['avg_presence_share'] * 100)} %"
         if k.get('avg_presence_share') is not None else "—"],
        ["Ø Bewegung (Radar)",
         f"{round(k['avg_movement_share'] * 100)} %"
         if k.get('avg_movement_share') is not None else "—"],
        ["Schnarch-Tage (selbst gemeldet)",
         f"{k['snore_events']} von {k['snore_documented']} dokumentiert"
         if k.get('snore_documented', 0) > 0
         else "— (keine Angaben)"],
    ]
    t = Table(kpi_rows, colWidths=[7 * cm, 9 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), main_font),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5e5644")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#faf7ef")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e3dccb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(PageBreak())

    # ─── Tages-Detail (1 Seite pro Tag) ─────────────────────
    for d in data["days"]:
        prev_date = d.get("evening_date")
        # Header: "Sa, 16.05.2026 — Nacht 15.05. → 16.05."
        header_main = f"{d['weekday']}, {d['date'].strftime('%d.%m.%Y')}"
        if prev_date:
            header_sub = (
                f"Nacht {prev_date.strftime('%d.%m.')} → "
                f"{d['date'].strftime('%d.%m.')}"
            )
            story.append(Paragraph(
                f"{header_main}  <font size='10' color='#9a8e76'>·  "
                f"{header_sub}</font>", h1))
        else:
            story.append(Paragraph(header_main, h1))

        ev = d["diary_evening"]
        mo = d["diary_morning"]
        sw = d["diary_smartwatch"]
        sens = d["sensors"]

        # Abendprotokoll (vom Vortag)
        ev_label = "Abendprotokoll"
        if prev_date:
            ev_label = f"Abendprotokoll (Vortag, {prev_date.strftime('%d.%m.')})"
        story.append(Paragraph(ev_label, h2))
        if ev:
            rows = [
                ["Leistungsfähigkeit", _fmt(ev.get("leistungsfaehigkeit"), " / 6")],
                ["Erschöpfung tags", _fmt(ev.get("erschoepfung_tags"), " / 3")],
                ["Tagschlaf", f"{ev.get('nap_dauer_min') or 0} min "
                              f"({_fmt_time(ev.get('nap_uhrzeit_von'))}"
                              f"–{_fmt_time(ev.get('nap_uhrzeit_bis'))})"
                              if ev.get("nap_dauer_min") else "—"],
                ["Koffein", f"{ev.get('koffein_anzahl') or 0} Getränke, "
                            f"letzte: {_fmt_time(ev.get('koffein_letzte_uhrzeit'))}"],
                ["Alkohol", f"{ev.get('alkohol_was') or '—'} "
                            f"({ev.get('alkohol_menge') or '—'})"
                            if ev.get('alkohol_was') else "—"],
                ["Bewegung",
                 f"{ev.get('bewegung_art') or '—'}, "
                 f"{ev.get('bewegung_dauer_min') or 0} min"
                 if ev.get('bewegung_art') else "—"],
                ["Stresslevel", _fmt(ev.get('stresslevel'), " / 5")],
                ["Bildschirmzeit", _fmt(ev.get('bildschirmzeit'))],
                ["Stimmung Abend", _fmt(ev.get('stimmung_abend'), " / 6")],
                ["Zu Bett", _fmt_time(ev.get('zubett_uhrzeit'))],
                ["Raumtemp. subjektiv", ev.get('raumtemp_subjektiv') or "—"],
            ]
            if u.get("gender") == "weiblich":
                rows.append(["Zyklusphase", ev.get("zyklus_phase") or "—"])
                rows.append(["Menstruationsbeschwerden",
                             _fmt(ev.get("menstruation_beschwerden"), " / 3")])
            t = Table(rows, colWidths=[6 * cm, 10 * cm])
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), main_font),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5e5644")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(t)
        else:
            story.append(Paragraph("<i>Kein Abendprotokoll</i>", small))
        story.append(Spacer(1, 6))

        # Morgenprotokoll
        story.append(Paragraph("Morgenprotokoll", h2))
        if mo:
            rows = [
                ["Stimmung Morgen", _fmt(mo.get('stimmung_morgen'), " / 6")],
                ["Erholsamkeit", _fmt(mo.get('erholsam'), " / 5")],
                ["Einschlaflatenz",
                 _fmt(mo.get('einschlaflatenz_min'), " min")],
                ["Nächtliches Erwachen",
                 f"{mo.get('naechtlich_wach_anzahl') or 0}× / "
                 f"{mo.get('naechtlich_wach_dauer_min') or 0} min"],
                ["Aufgewacht / Aufgestanden",
                 f"{_fmt_time(mo.get('aufgewacht_uhrzeit'))} / "
                 f"{_fmt_time(mo.get('aufgestanden_uhrzeit'))}"],
                ["Geschlafen gesamt",
                 f"{mo.get('geschlafen_h') or 0} h "
                 f"{mo.get('geschlafen_min') or 0} min"],
                ["Schlafmedikament",
                 f"{mo.get('schlafmedikament') or '—'} "
                 f"({mo.get('schlafmedikament_dosis') or '—'}, "
                 f"{_fmt_time(mo.get('schlafmedikament_uhrzeit'))})"
                 if mo.get('schlafmedikament') else "—"],
                ["Schlafposition", mo.get('schlafposition') or "—"],
                ["Traum erinnert / Valenz",
                 f"{_fmt(mo.get('traum_erinnert'))} / "
                 f"{mo.get('traum_valenz') or '—'}"],
                ["Schmerzen", _fmt(mo.get('schmerzen_aufwachen'))],
                ["Schnarchen (selbst)",
                 _fmt(mo.get('schnarchen_selbst'), " / 3")],
            ]
            t = Table(rows, colWidths=[6 * cm, 10 * cm])
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), main_font),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5e5644")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(t)
        else:
            story.append(Paragraph("<i>Kein Morgenprotokoll</i>", small))
        story.append(Spacer(1, 6))

        # Smartwatch
        story.append(Paragraph("Smartwatch", h2))
        if sw:
            def hm(minutes):
                if minutes is None:
                    return "—"
                return f"{minutes // 60} h {minutes % 60} min"

            # Schlafphasen-Anteile: Prozent vom Gesamtschlaf (ohne Wach)
            # Apple-Konvention: Gesamtschlaf = Tief + Leicht + REM (Wach separat)
            tief = sw.get('tiefschlaf_min') or 0
            leicht = sw.get('leichtschlaf_min') or 0
            rem = sw.get('rem_min') or 0
            phases_sum = tief + leicht + rem
            if phases_sum > 0:
                pct_tief = round(100 * tief / phases_sum, 1)
                pct_leicht = round(100 * leicht / phases_sum, 1)
                pct_rem = round(100 * rem / phases_sum, 1)
                phases_pct_str = (
                    f"Tief {pct_tief}% · Leicht {pct_leicht}% · REM {pct_rem}%"
                )
            else:
                phases_pct_str = "—"

            rows = [
                ["Plattform", sw.get('platform') or '—'],
                ["Gesamtschlaf", hm(sw.get('gesamtschlaf_min'))],
                ["Tiefschlaf / Leichtschlaf / REM",
                 f"{hm(sw.get('tiefschlaf_min'))} / "
                 f"{hm(sw.get('leichtschlaf_min'))} / "
                 f"{hm(sw.get('rem_min'))}"],
                ["Phasen-Anteile", phases_pct_str],
                ["Wachphasen", hm(sw.get('wach_min'))],
                ["Herzfrequenz (Min / Ø / Max)",
                 f"{_fmt(sw.get('herzfrequenz_min'))} / "
                 f"{_fmt(sw.get('herzfrequenz_avg'))} / "
                 f"{_fmt(sw.get('herzfrequenz_max'))} bpm"],
                ["HRV Ø", _fmt(
                    float(sw['hrv']) if sw.get('hrv') else None, " ms")],
                ["SpO₂ Blutsauerstoff (Min / Ø / Max)",
                 f"{_fmt(float(sw['spo2_min']) if sw.get('spo2_min') else None)} / "
                 f"{_fmt(float(sw['spo2_avg']) if sw.get('spo2_avg') else None)} / "
                 f"{_fmt(float(sw['spo2_max']) if sw.get('spo2_max') else None)} %"],
                ["Atemfrequenz (Min / Ø / Max)",
                 f"{_fmt(float(sw['atemfrequenz_min']) if sw.get('atemfrequenz_min') else None)} / "
                 f"{_fmt(float(sw['atemfrequenz_avg']) if sw.get('atemfrequenz_avg') else None)} / "
                 f"{_fmt(float(sw['atemfrequenz_max']) if sw.get('atemfrequenz_max') else None)} /min"],
                ["Atemstörungen / Apnoe-Risiko",
                 sw.get('apnoe_risiko') or "—"],
                ["Schritte", _fmt(sw.get('schritte'))],
            ]
            t = Table(rows, colWidths=[7 * cm, 9 * cm])
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), main_font),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5e5644")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(t)
        else:
            story.append(Paragraph("<i>Keine Smartwatch-Daten</i>", small))
        story.append(Spacer(1, 6))

        # Sensoren (Nacht-Aggregate)
        ns = d.get("night_start")
        ne = d.get("night_end")
        if ns and ne:
            window_label = (
                f"{ns.strftime('%d.%m. %H:%M')} → {ne.strftime('%d.%m. %H:%M')}"
            )
            if d.get("night_source") == "fallback_2208":
                window_label += " (Fallback)"
            story.append(Paragraph(f"Sensoren (Nacht {window_label})", h2))
        else:
            story.append(Paragraph("Sensoren (Nacht)", h2))
        if sens.get("has_data"):
            rows = []
            if "bme688" in sens:
                b = sens["bme688"]
                rows.append([
                    "Temperatur (Min / Ø / Max)",
                    f"{_fmt(float(b['temp_min']) if b.get('temp_min') else None, ' °C')} / "
                    f"{_fmt(float(b['temp_avg']) if b.get('temp_avg') else None, ' °C')} / "
                    f"{_fmt(float(b['temp_max']) if b.get('temp_max') else None, ' °C')}"
                ])
                rows.append([
                    "Ø Luftfeuchte",
                    _fmt(float(b['hum_avg']) if b.get('hum_avg') else None, " %")
                ])
                rows.append([
                    "Ø Luftdruck",
                    _fmt(float(b['press_avg']) if b.get('press_avg') else None, " hPa")
                ])
            if "bh1750" in sens:
                l = sens["bh1750"]
                rows.append([
                    "Licht (Min / Ø / Max)",
                    f"{_fmt(float(l['lux_min']) if l.get('lux_min') else None, ' lx')} / "
                    f"{_fmt(float(l['lux_avg']) if l.get('lux_avg') else None, ' lx')} / "
                    f"{_fmt(float(l['lux_max']) if l.get('lux_max') else None, ' lx')}"
                ])
            if "co2" in sens:
                c = sens["co2"]
                rows.append([
                    "CO₂ (Min / Ø / Max)",
                    f"{_fmt(c.get('co2_min'), ' ppm')} / "
                    f"{_fmt(float(c['co2_avg']) if c.get('co2_avg') else None, ' ppm')} / "
                    f"{_fmt(c.get('co2_max'), ' ppm')}"
                ])
            if "dust" in sens:
                p = sens["dust"]
                rows.append([
                    "Ø Feinstaub PM1 / 2.5 / 10",
                    f"{_fmt(float(p['pm1_avg']) if p.get('pm1_avg') else None)} / "
                    f"{_fmt(float(p['pm25_avg']) if p.get('pm25_avg') else None)} / "
                    f"{_fmt(float(p['pm10_avg']) if p.get('pm10_avg') else None)} µg/m³"
                ])
            if "radar" in sens:
                rd = sens["radar"]
                ps = rd.get("presence_share_avg")
                ms = rd.get("movement_share_avg")
                da = rd.get("distance_avg")
                rows.append([
                    "Radar Präsenz / Bewegung",
                    f"{round(float(ps) * 100) if ps is not None else '—'} % / "
                    f"{round(float(ms) * 100) if ms is not None else '—'} % "
                    f"(Ø Distanz {_fmt(float(da) if da is not None else None, ' cm')})"
                ])
            if "audio" in sens:
                rows.append(["Audio-Messpunkte", str(sens["audio"]["n"])])
            t = Table(rows, colWidths=[6 * cm, 10 * cm])
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), main_font),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5e5644")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(t)
        else:
            story.append(Paragraph(
                "<i>Keine Sensordaten in diesem Zeitfenster</i>", small))

        if d is not data["days"][-1]:
            story.append(PageBreak())

    doc.build(story)
    return buf.getvalue()


# ── CSV-Zip-Generierung ────────────────────────────────────
def _rows_to_csv(rows, fieldnames=None):
    """Serialisiert RealDictRow-Liste → CSV-Bytes."""
    if not rows:
        return b""
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        clean = {}
        for k in fieldnames:
            v = r.get(k)
            clean[k] = "" if v is None else v
        writer.writerow(clean)
    return out.getvalue().encode("utf-8")


def _build_csv_zip(data: dict) -> bytes:
    """Erzeugt ein Zip-Archiv mit 5 CSV-Files."""
    days = data["days"]

    evenings = [d["diary_evening"] for d in days if d["diary_evening"]]
    mornings = [d["diary_morning"] for d in days if d["diary_morning"]]
    watches = [d["diary_smartwatch"] for d in days if d["diary_smartwatch"]]

    # Sensors: pro Tag eine Zeile mit den Aggregaten
    sensor_rows = []
    for d in days:
        row = {"date": d["date"].isoformat(), "weekday": d["weekday"]}
        s = d["sensors"]
        for key in ("bme688", "bh1750", "co2", "dust", "radar", "audio"):
            blk = s.get(key)
            if not blk:
                continue
            for col, val in blk.items():
                row[f"{key}_{col}"] = val
        sensor_rows.append(row)

    u = data["user"]
    profile_row = [{k: u.get(k) for k in u.keys()}] if u else []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("profile.csv", _rows_to_csv(profile_row))
        zf.writestr("diary_evening.csv", _rows_to_csv(evenings))
        zf.writestr("diary_morning.csv", _rows_to_csv(mornings))
        zf.writestr("diary_smartwatch.csv", _rows_to_csv(watches))
        zf.writestr("sensors_daily.csv", _rows_to_csv(sensor_rows))

        summary = _build_summary(data)
        # KPI-Zusammenfassung als CSV
        kpi_row = {"start_date": summary["start_date"],
                   "end_date": summary["end_date"], **summary["kpis"]}
        zf.writestr("summary.csv", _rows_to_csv([kpi_row]))

    return buf.getvalue()


# ── Endpoints ──────────────────────────────────────────────
def _resolve_period(start_date: Optional[date], end_date: date):
    """Hilfsfunktion: start_date defaulten auf end_date - 6 (legacy 7d)."""
    if start_date is None:
        start_date = end_date - timedelta(days=6)
    return start_date, end_date


@app.get("/api/reports/weekly/{user_id}/{end_date}/summary")
def report_period_summary(
    user_id: str,
    end_date: date,
    start_date: Optional[date] = None,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """JSON-Vorschau für die Report-Card.

    Wenn start_date NICHT gesetzt → 7-Tage-Fenster bis end_date.
    Mit start_date → freier Zeitraum.
    """
    if not user["is_admin"] and user["username"] != user_id:
        raise HTTPException(403, "Nicht erlaubt")
    s, e = _resolve_period(start_date, end_date)
    data = _fetch_period_data(db, user_id, s, e)
    return _build_summary(data)


@app.get("/api/reports/weekly/{user_id}/{end_date}/pdf")
def report_period_pdf(
    user_id: str,
    end_date: date,
    start_date: Optional[date] = None,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """PDF-Download des Datenreports."""
    if not user["is_admin"] and user["username"] != user_id:
        raise HTTPException(403, "Nicht erlaubt")
    s, e = _resolve_period(start_date, end_date)
    data = _fetch_period_data(db, user_id, s, e)
    pdf_bytes = _build_pdf(data)
    fname = (f"sleepkit_{user_id}_{s.isoformat()}_bis_{e.isoformat()}.pdf")
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/reports/weekly/{user_id}/{end_date}/csv")
def report_period_csv(
    user_id: str,
    end_date: date,
    start_date: Optional[date] = None,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """ZIP mit allen CSV-Daten des Datenreports."""
    if not user["is_admin"] and user["username"] != user_id:
        raise HTTPException(403, "Nicht erlaubt")
    s, e = _resolve_period(start_date, end_date)
    data = _fetch_period_data(db, user_id, s, e)
    zip_bytes = _build_csv_zip(data)
    fname = (f"sleepkit_{user_id}_{s.isoformat()}_bis_{e.isoformat()}.zip")
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
