CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS intelligence;

CREATE TABLE IF NOT EXISTS intelligence.hotels (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  address TEXT NOT NULL,
  lat DOUBLE PRECISION CHECK (lat BETWEEN -90 AND 90),
  lon DOUBLE PRECISION CHECK (lon BETWEEN -180 AND 180),
  coord_status TEXT NOT NULL,
  offer_id TEXT UNIQUE,
  location GEOGRAPHY(POINT,4326) GENERATED ALWAYS AS
    (CASE WHEN lat IS NULL OR lon IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography END) STORED
);
CREATE INDEX IF NOT EXISTS idx_intelligence_hotels_location ON intelligence.hotels USING gist(location);

CREATE TABLE IF NOT EXISTS intelligence.hotel_external_ids (
  hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  offer_id BIGINT,
  identity_method TEXT NOT NULL,
  confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  verified_at TIMESTAMPTZ,
  PRIMARY KEY (provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_intelligence_offer ON intelligence.hotel_external_ids(offer_id);

CREATE TABLE IF NOT EXISTS intelligence.feature_definitions (
  key TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  definition JSONB NOT NULL,
  registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(key, version)
);

CREATE TABLE IF NOT EXISTS intelligence.source_records (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  url TEXT NOT NULL,
  payload TEXT NOT NULL CHECK (jsonb_typeof(payload::jsonb) IS NOT NULL),
  payload_sha256 TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  provenance TEXT NOT NULL,
  license TEXT NOT NULL,
  UNIQUE(provider, external_id, payload_sha256)
);
CREATE INDEX IF NOT EXISTS idx_intelligence_source_provider ON intelligence.source_records(provider, external_id);

CREATE TABLE IF NOT EXISTS intelligence.assertions (
  id TEXT PRIMARY KEY,
  hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
  feature_key TEXT NOT NULL,
  value_json TEXT NOT NULL CHECK (jsonb_typeof(value_json::jsonb) IS NOT NULL),
  unit TEXT,
  source_id TEXT NOT NULL REFERENCES intelligence.source_records(id),
  method TEXT NOT NULL,
  confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  observed_at TEXT NOT NULL,
  scope TEXT NOT NULL,
  context_json TEXT NOT NULL CHECK (jsonb_typeof(context_json::jsonb) IS NOT NULL),
  algorithm_version TEXT,
  input_source_ids TEXT NOT NULL CHECK (jsonb_typeof(input_source_ids::jsonb) IS NOT NULL),
  UNIQUE(hotel_id, feature_key, value_json, source_id, scope, context_json, algorithm_version)
);
CREATE INDEX IF NOT EXISTS idx_intelligence_assertion_hotel ON intelligence.assertions(hotel_id, feature_key);

CREATE TABLE IF NOT EXISTS intelligence.resolved_facts (
  hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
  feature_key TEXT NOT NULL,
  context_json TEXT NOT NULL CHECK (jsonb_typeof(context_json::jsonb) IS NOT NULL),
  value_json TEXT CHECK (jsonb_typeof(value_json::jsonb) IS NOT NULL),
  status TEXT NOT NULL CHECK (status IN ('verified','estimated','reported','conflicted','unknown','stale')),
  assertion_ids TEXT NOT NULL CHECK (jsonb_typeof(assertion_ids::jsonb) IS NOT NULL),
  rationale TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(hotel_id,feature_key,context_json)
);

CREATE TABLE IF NOT EXISTS intelligence.geo_features (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES intelligence.source_records(id),
  category TEXT NOT NULL,
  geom GEOGRAPHY(GEOMETRY,4326) NOT NULL,
  valid_from DATE,
  valid_to DATE,
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);
CREATE INDEX IF NOT EXISTS idx_intelligence_geo_geom ON intelligence.geo_features USING gist(geom);

CREATE TABLE IF NOT EXISTS intelligence.questions (
  id TEXT PRIMARY KEY,
  hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
  feature_keys TEXT NOT NULL CHECK (jsonb_typeof(feature_keys::jsonb) IS NOT NULL),
  question TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intelligence.answers (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL REFERENCES intelligence.questions(id),
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intelligence.candidates (
  id TEXT PRIMARY KEY,
  answer_id TEXT NOT NULL REFERENCES intelligence.answers(id),
  feature_key TEXT NOT NULL,
  value_json TEXT NOT NULL CHECK (jsonb_typeof(value_json::jsonb) IS NOT NULL),
  evidence_text TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intelligence.jobs (
  id TEXT PRIMARY KEY,
  hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
  feature_keys TEXT NOT NULL CHECK (jsonb_typeof(feature_keys::jsonb) IS NOT NULL),
  mode TEXT NOT NULL CHECK (mode IN ('demo','live')),
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL CHECK (jsonb_typeof(details_json::jsonb) IS NOT NULL),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intelligence.audit_events (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  hotel_id TEXT,
  detail_json TEXT NOT NULL CHECK (jsonb_typeof(detail_json::jsonb) IS NOT NULL),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intelligence_audit_time ON intelligence.audit_events(created_at DESC);
