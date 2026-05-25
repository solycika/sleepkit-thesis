--
-- PostgreSQL database dump
--

\restrict b723p2QnV1tfpB9uBmInxWlDtK6NG1fatmRui8xJnYBv1zzhR6yskO25dW0j9nD

-- Dumped from database version 18.4 (Ubuntu 18.4-0ubuntu0.26.04.1)
-- Dumped by pg_dump version 18.4 (Ubuntu 18.4-0ubuntu0.26.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: cycle_phase_t; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.cycle_phase_t AS ENUM (
    'follikel',
    'ovulation',
    'luteal',
    'menstruation',
    'na'
);


--
-- Name: dream_valence_t; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.dream_valence_t AS ENUM (
    'positiv',
    'neutral',
    'negativ',
    'gemischt'
);


--
-- Name: gender_t; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.gender_t AS ENUM (
    'weiblich',
    'maennlich',
    'divers',
    'keine_angabe'
);


--
-- Name: meal_size_t; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.meal_size_t AS ENUM (
    'leicht',
    'mittel',
    'schwer'
);


--
-- Name: room_temp_subj_t; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.room_temp_subj_t AS ENUM (
    'zu_kalt',
    'angenehm',
    'zu_warm'
);


--
-- Name: sleep_position_t; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.sleep_position_t AS ENUM (
    'ruecken',
    'seite',
    'bauch',
    'wechselnd'
);


--
-- Name: user_role_t; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.user_role_t AS ENUM (
    'participant',
    'admin'
);


--
-- Name: trg_set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: diary_evening; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.diary_evening (
    id bigint NOT NULL,
    user_id text NOT NULL,
    diary_date date NOT NULL,
    leistungsfaehigkeit smallint,
    erschoepfung_tags smallint,
    nap_dauer_min smallint,
    nap_uhrzeit_von time without time zone,
    nap_uhrzeit_bis time without time zone,
    alkohol_was text,
    alkohol_menge text,
    zubett_uhrzeit time without time zone,
    stimmung_abend smallint,
    koffein_anzahl smallint,
    koffein_letzte_uhrzeit time without time zone,
    bewegung_art text,
    bewegung_dauer_min smallint,
    bewegung_uhrzeit time without time zone,
    stresslevel smallint,
    bildschirmzeit boolean,
    bildschirmzeit_geraet text,
    besondere_ereignisse text,
    abendmahlzeit_uhrzeit time without time zone,
    abendmahlzeit_groesse public.meal_size_t,
    raumtemp_subjektiv public.room_temp_subj_t,
    zyklus_phase public.cycle_phase_t,
    menstruation_beschwerden smallint,
    schwangerschaftswoche smallint,
    hitzewallungen_anzahl smallint,
    hormonelle_verhuetung boolean,
    hormonelle_verhuetung_art text,
    nykturie_toilettengaenge smallint,
    energieniveau_subj smallint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT diary_evening_energieniveau_subj_check CHECK (((energieniveau_subj >= 1) AND (energieniveau_subj <= 5))),
    CONSTRAINT diary_evening_erschoepfung_tags_check CHECK (((erschoepfung_tags >= 0) AND (erschoepfung_tags <= 3))),
    CONSTRAINT diary_evening_leistungsfaehigkeit_check CHECK (((leistungsfaehigkeit >= 1) AND (leistungsfaehigkeit <= 6))),
    CONSTRAINT diary_evening_menstruation_beschwerden_check CHECK (((menstruation_beschwerden >= 0) AND (menstruation_beschwerden <= 3))),
    CONSTRAINT diary_evening_stimmung_abend_check CHECK (((stimmung_abend >= 1) AND (stimmung_abend <= 6))),
    CONSTRAINT diary_evening_stresslevel_check CHECK (((stresslevel >= 1) AND (stresslevel <= 5)))
);


--
-- Name: diary_evening_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.diary_evening_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diary_evening_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.diary_evening_id_seq OWNED BY public.diary_evening.id;


--
-- Name: diary_morning; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.diary_morning (
    id bigint NOT NULL,
    user_id text NOT NULL,
    diary_date date NOT NULL,
    stimmung_morgen smallint,
    erholsam smallint,
    einschlaflatenz_min smallint,
    naechtlich_wach_anzahl smallint,
    naechtlich_wach_dauer_min smallint,
    aufgewacht_uhrzeit time without time zone,
    geschlafen_h smallint,
    geschlafen_min smallint,
    aufgestanden_uhrzeit time without time zone,
    schlafmedikament text,
    schlafmedikament_dosis text,
    schlafmedikament_uhrzeit time without time zone,
    schlafposition public.sleep_position_t,
    traum_erinnert boolean,
    traum_valenz public.dream_valence_t,
    schmerzen_aufwachen boolean,
    schmerzen_lokation text,
    schmerzen_intensitaet smallint,
    toilettengaenge_anzahl smallint,
    schnarchen_selbst smallint,
    schnarchen_partner text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT diary_morning_erholsam_check CHECK (((erholsam >= 1) AND (erholsam <= 5))),
    CONSTRAINT diary_morning_schmerzen_intensitaet_check CHECK (((schmerzen_intensitaet >= 0) AND (schmerzen_intensitaet <= 10))),
    CONSTRAINT diary_morning_schnarchen_selbst_check CHECK (((schnarchen_selbst >= 0) AND (schnarchen_selbst <= 3))),
    CONSTRAINT diary_morning_stimmung_morgen_check CHECK (((stimmung_morgen >= 1) AND (stimmung_morgen <= 6)))
);


--
-- Name: diary_morning_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.diary_morning_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diary_morning_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.diary_morning_id_seq OWNED BY public.diary_morning.id;


--
-- Name: diary_smartwatch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.diary_smartwatch (
    id bigint NOT NULL,
    user_id text NOT NULL,
    diary_date date NOT NULL,
    platform text,
    gesamtschlaf_min smallint,
    tiefschlaf_min smallint,
    leichtschlaf_min smallint,
    rem_min smallint,
    wach_min smallint,
    herzfrequenz_avg smallint,
    herzfrequenz_min smallint,
    herzfrequenz_max smallint,
    hrv numeric(5,2),
    spo2_avg numeric(4,1),
    spo2_min numeric(4,1),
    schritte integer,
    atemfrequenz_avg numeric(4,1),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    hrv_min numeric(5,2),
    hrv_max numeric(5,2),
    spo2_max numeric(4,1),
    atemfrequenz_min numeric(4,1),
    atemfrequenz_max numeric(4,1),
    apnoe_risiko text
);


--
-- Name: diary_smartwatch_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.diary_smartwatch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diary_smartwatch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.diary_smartwatch_id_seq OWNED BY public.diary_smartwatch.id;


--
-- Name: sensor_audio; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensor_audio (
    id bigint NOT NULL,
    session_id text NOT NULL,
    ts timestamp with time zone NOT NULL,
    rms_left numeric(10,6),
    rms_right numeric(10,6),
    peak_left numeric(10,6),
    peak_right numeric(10,6),
    snore_score numeric(6,4),
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sensor_audio_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sensor_audio_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sensor_audio_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sensor_audio_id_seq OWNED BY public.sensor_audio.id;


--
-- Name: sensor_bh1750; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensor_bh1750 (
    id bigint NOT NULL,
    session_id text NOT NULL,
    ts timestamp with time zone NOT NULL,
    lux numeric(8,1),
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sensor_bh1750_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sensor_bh1750_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sensor_bh1750_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sensor_bh1750_id_seq OWNED BY public.sensor_bh1750.id;


--
-- Name: sensor_bme688; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensor_bme688 (
    id bigint NOT NULL,
    session_id text NOT NULL,
    ts timestamp with time zone NOT NULL,
    temperature numeric(5,2),
    humidity numeric(5,2),
    pressure numeric(7,2),
    gas_resistance numeric(12,0),
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sensor_bme688_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sensor_bme688_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sensor_bme688_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sensor_bme688_id_seq OWNED BY public.sensor_bme688.id;


--
-- Name: sensor_co2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensor_co2 (
    id bigint NOT NULL,
    session_id text NOT NULL,
    ts timestamp with time zone NOT NULL,
    co2_ppm integer,
    temperature numeric(5,2),
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sensor_co2_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sensor_co2_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sensor_co2_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sensor_co2_id_seq OWNED BY public.sensor_co2.id;


--
-- Name: sensor_dust; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensor_dust (
    id bigint NOT NULL,
    session_id text NOT NULL,
    ts timestamp with time zone NOT NULL,
    pm1_0 integer,
    pm2_5 integer,
    pm10 integer,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sensor_dust_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sensor_dust_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sensor_dust_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sensor_dust_id_seq OWNED BY public.sensor_dust.id;


--
-- Name: sensor_radar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensor_radar (
    id bigint NOT NULL,
    session_id text NOT NULL,
    ts timestamp with time zone NOT NULL,
    presence smallint,
    distance_cm integer,
    energy integer,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sensor_radar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sensor_radar_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sensor_radar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sensor_radar_id_seq OWNED BY public.sensor_radar.id;


--
-- Name: sensor_radar_minute; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensor_radar_minute (
    id bigint NOT NULL,
    session_id text NOT NULL,
    ts_minute timestamp with time zone NOT NULL,
    n_samples integer NOT NULL,
    presence_share numeric(4,3) NOT NULL,
    movement_share numeric(4,3) NOT NULL,
    distance_avg numeric(7,1),
    distance_min integer,
    distance_max integer,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sensor_radar_minute_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sensor_radar_minute_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sensor_radar_minute_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sensor_radar_minute_id_seq OWNED BY public.sensor_radar_minute.id;


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    session_id text NOT NULL,
    user_id text NOT NULL,
    user_name text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_id text NOT NULL,
    cognito_sub text,
    role public.user_role_t DEFAULT 'participant'::public.user_role_t NOT NULL,
    user_name text NOT NULL,
    email text,
    age smallint,
    gender public.gender_t,
    height_cm smallint,
    weight_kg numeric(5,2),
    medications text,
    chronic_issues text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    verhuetung_aktuell boolean,
    verhuetung_seit date,
    verhuetung_art text
);


--
-- Name: COLUMN users.verhuetung_aktuell; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.verhuetung_aktuell IS 'Nutzt die Teilnehmerin aktuell Verhuetung? Nur bei gender=weiblich relevant.';


--
-- Name: COLUMN users.verhuetung_seit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.verhuetung_seit IS 'Startdatum der aktuellen Verhuetung (optional).';


--
-- Name: COLUMN users.verhuetung_art; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.verhuetung_art IS 'Kategorie: pille_kombi, pille_minipille, hormonimplantat, hormonspirale, kupferspirale, barriere, sterilisation, nfp, sonstiges, keine_angabe';


--
-- Name: diary_evening id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_evening ALTER COLUMN id SET DEFAULT nextval('public.diary_evening_id_seq'::regclass);


--
-- Name: diary_morning id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_morning ALTER COLUMN id SET DEFAULT nextval('public.diary_morning_id_seq'::regclass);


--
-- Name: diary_smartwatch id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_smartwatch ALTER COLUMN id SET DEFAULT nextval('public.diary_smartwatch_id_seq'::regclass);


--
-- Name: sensor_audio id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_audio ALTER COLUMN id SET DEFAULT nextval('public.sensor_audio_id_seq'::regclass);


--
-- Name: sensor_bh1750 id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_bh1750 ALTER COLUMN id SET DEFAULT nextval('public.sensor_bh1750_id_seq'::regclass);


--
-- Name: sensor_bme688 id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_bme688 ALTER COLUMN id SET DEFAULT nextval('public.sensor_bme688_id_seq'::regclass);


--
-- Name: sensor_co2 id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_co2 ALTER COLUMN id SET DEFAULT nextval('public.sensor_co2_id_seq'::regclass);


--
-- Name: sensor_dust id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_dust ALTER COLUMN id SET DEFAULT nextval('public.sensor_dust_id_seq'::regclass);


--
-- Name: sensor_radar id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_radar ALTER COLUMN id SET DEFAULT nextval('public.sensor_radar_id_seq'::regclass);


--
-- Name: sensor_radar_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_radar_minute ALTER COLUMN id SET DEFAULT nextval('public.sensor_radar_minute_id_seq'::regclass);


--
-- Name: diary_evening diary_evening_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_evening
    ADD CONSTRAINT diary_evening_pkey PRIMARY KEY (id);


--
-- Name: diary_evening diary_evening_user_id_diary_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_evening
    ADD CONSTRAINT diary_evening_user_id_diary_date_key UNIQUE (user_id, diary_date);


--
-- Name: diary_morning diary_morning_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_morning
    ADD CONSTRAINT diary_morning_pkey PRIMARY KEY (id);


--
-- Name: diary_morning diary_morning_user_id_diary_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_morning
    ADD CONSTRAINT diary_morning_user_id_diary_date_key UNIQUE (user_id, diary_date);


--
-- Name: diary_smartwatch diary_smartwatch_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_smartwatch
    ADD CONSTRAINT diary_smartwatch_pkey PRIMARY KEY (id);


--
-- Name: diary_smartwatch diary_smartwatch_user_id_diary_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_smartwatch
    ADD CONSTRAINT diary_smartwatch_user_id_diary_date_key UNIQUE (user_id, diary_date);


--
-- Name: sensor_audio sensor_audio_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_audio
    ADD CONSTRAINT sensor_audio_pkey PRIMARY KEY (id);


--
-- Name: sensor_audio sensor_audio_session_id_ts_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_audio
    ADD CONSTRAINT sensor_audio_session_id_ts_key UNIQUE (session_id, ts);


--
-- Name: sensor_bh1750 sensor_bh1750_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_bh1750
    ADD CONSTRAINT sensor_bh1750_pkey PRIMARY KEY (id);


--
-- Name: sensor_bh1750 sensor_bh1750_session_id_ts_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_bh1750
    ADD CONSTRAINT sensor_bh1750_session_id_ts_key UNIQUE (session_id, ts);


--
-- Name: sensor_bme688 sensor_bme688_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_bme688
    ADD CONSTRAINT sensor_bme688_pkey PRIMARY KEY (id);


--
-- Name: sensor_bme688 sensor_bme688_session_id_ts_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_bme688
    ADD CONSTRAINT sensor_bme688_session_id_ts_key UNIQUE (session_id, ts);


--
-- Name: sensor_co2 sensor_co2_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_co2
    ADD CONSTRAINT sensor_co2_pkey PRIMARY KEY (id);


--
-- Name: sensor_co2 sensor_co2_session_id_ts_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_co2
    ADD CONSTRAINT sensor_co2_session_id_ts_key UNIQUE (session_id, ts);


--
-- Name: sensor_dust sensor_dust_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_dust
    ADD CONSTRAINT sensor_dust_pkey PRIMARY KEY (id);


--
-- Name: sensor_dust sensor_dust_session_id_ts_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_dust
    ADD CONSTRAINT sensor_dust_session_id_ts_key UNIQUE (session_id, ts);


--
-- Name: sensor_radar_minute sensor_radar_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_radar_minute
    ADD CONSTRAINT sensor_radar_minute_pkey PRIMARY KEY (id);


--
-- Name: sensor_radar_minute sensor_radar_minute_session_id_ts_minute_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_radar_minute
    ADD CONSTRAINT sensor_radar_minute_session_id_ts_minute_key UNIQUE (session_id, ts_minute);


--
-- Name: sensor_radar sensor_radar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_radar
    ADD CONSTRAINT sensor_radar_pkey PRIMARY KEY (id);


--
-- Name: sensor_radar sensor_radar_session_id_ts_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_radar
    ADD CONSTRAINT sensor_radar_session_id_ts_key UNIQUE (session_id, ts);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (session_id);


--
-- Name: users users_cognito_sub_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_cognito_sub_key UNIQUE (cognito_sub);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: idx_audio_session_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audio_session_ts ON public.sensor_audio USING btree (session_id, ts);


--
-- Name: idx_bh1750_session_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bh1750_session_ts ON public.sensor_bh1750 USING btree (session_id, ts);


--
-- Name: idx_bme688_session_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bme688_session_ts ON public.sensor_bme688 USING btree (session_id, ts);


--
-- Name: idx_co2_session_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_co2_session_ts ON public.sensor_co2 USING btree (session_id, ts);


--
-- Name: idx_diary_evening_user_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_diary_evening_user_date ON public.diary_evening USING btree (user_id, diary_date DESC);


--
-- Name: idx_diary_morning_user_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_diary_morning_user_date ON public.diary_morning USING btree (user_id, diary_date DESC);


--
-- Name: idx_dust_session_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dust_session_ts ON public.sensor_dust USING btree (session_id, ts);


--
-- Name: idx_radar_minute_session_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_radar_minute_session_ts ON public.sensor_radar_minute USING btree (session_id, ts_minute);


--
-- Name: idx_radar_session_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_radar_session_ts ON public.sensor_radar USING btree (session_id, ts);


--
-- Name: idx_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_user ON public.sessions USING btree (user_id, started_at DESC);


--
-- Name: idx_smartwatch_user_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_smartwatch_user_date ON public.diary_smartwatch USING btree (user_id, diary_date DESC);


--
-- Name: idx_users_cognito; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_cognito ON public.users USING btree (cognito_sub);


--
-- Name: diary_evening trg_diary_evening_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_diary_evening_updated_at BEFORE UPDATE ON public.diary_evening FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: diary_morning trg_diary_morning_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_diary_morning_updated_at BEFORE UPDATE ON public.diary_morning FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: diary_smartwatch trg_diary_smartwatch_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_diary_smartwatch_updated_at BEFORE UPDATE ON public.diary_smartwatch FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: users trg_users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- Name: diary_evening diary_evening_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_evening
    ADD CONSTRAINT diary_evening_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: diary_morning diary_morning_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_morning
    ADD CONSTRAINT diary_morning_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: diary_smartwatch diary_smartwatch_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diary_smartwatch
    ADD CONSTRAINT diary_smartwatch_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: sensor_audio sensor_audio_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_audio
    ADD CONSTRAINT sensor_audio_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(session_id);


--
-- Name: sensor_bh1750 sensor_bh1750_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_bh1750
    ADD CONSTRAINT sensor_bh1750_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(session_id);


--
-- Name: sensor_bme688 sensor_bme688_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_bme688
    ADD CONSTRAINT sensor_bme688_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(session_id);


--
-- Name: sensor_co2 sensor_co2_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_co2
    ADD CONSTRAINT sensor_co2_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(session_id);


--
-- Name: sensor_dust sensor_dust_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_dust
    ADD CONSTRAINT sensor_dust_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(session_id);


--
-- Name: sensor_radar_minute sensor_radar_minute_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_radar_minute
    ADD CONSTRAINT sensor_radar_minute_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(session_id);


--
-- Name: sensor_radar sensor_radar_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensor_radar
    ADD CONSTRAINT sensor_radar_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(session_id);


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- PostgreSQL database dump complete
--

\unrestrict b723p2QnV1tfpB9uBmInxWlDtK6NG1fatmRui8xJnYBv1zzhR6yskO25dW0j9nD

