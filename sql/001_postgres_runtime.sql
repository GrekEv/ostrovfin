CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS intelligence;

CREATE TABLE IF NOT EXISTS intelligence.hotels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL CHECK(lat BETWEEN -90 AND 90),
    lon DOUBLE PRECISION NOT NULL CHECK(lon BETWEEN -180 AND 180),
    coord_status TEXT NOT NULL,
    offer_id TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS hotels_location_gist ON intelligence.hotels
    USING GIST((ST_SetSRID(ST_MakePoint(lon,lat),4326)::geography));

CREATE TABLE IF NOT EXISTS intelligence.hotel_external_ids (
    hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    travel_card_id INTEGER,
    match_method TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    reviewed_at TIMESTAMPTZ,
    PRIMARY KEY(provider,external_id)
);
CREATE INDEX IF NOT EXISTS idx_intel_travel_card ON intelligence.hotel_external_ids(travel_card_id);

CREATE TABLE IF NOT EXISTS intelligence.source_records (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    provenance TEXT NOT NULL,
    license TEXT NOT NULL,
    UNIQUE(provider,external_id,payload_sha256)
);
CREATE INDEX IF NOT EXISTS idx_intel_sources_provider ON intelligence.source_records(provider,external_id);

CREATE TABLE IF NOT EXISTS intelligence.source_geometries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES intelligence.source_records(id),
    segment_sha256 TEXT NOT NULL,
    geom geometry(LineString,4326) NOT NULL,
    UNIQUE(source_id,segment_sha256)
);
CREATE INDEX IF NOT EXISTS idx_intel_source_geometries_geom
    ON intelligence.source_geometries USING GIST(geom);

CREATE TABLE IF NOT EXISTS intelligence.assertions (
    id TEXT PRIMARY KEY,
    hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
    feature_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    unit TEXT,
    source_id TEXT NOT NULL REFERENCES intelligence.source_records(id),
    method TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    observed_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    context_json TEXT NOT NULL,
    algorithm_version TEXT,
    input_source_ids TEXT NOT NULL,
    UNIQUE(hotel_id,feature_key,value_json,source_id,scope,context_json,algorithm_version)
);
CREATE INDEX IF NOT EXISTS idx_intel_assertions_lookup ON intelligence.assertions(hotel_id,feature_key);

CREATE TABLE IF NOT EXISTS intelligence.resolved_facts (
    hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
    feature_key TEXT NOT NULL,
    context_json TEXT NOT NULL,
    value_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('verified','estimated','reported','conflicted','unknown','stale')),
    assertion_ids TEXT NOT NULL,
    rationale TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(hotel_id,feature_key,context_json)
);

CREATE TABLE IF NOT EXISTS intelligence.questions (
    id TEXT PRIMARY KEY,
    hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
    feature_keys TEXT NOT NULL,
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
    value_json TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intelligence.jobs (
    id TEXT PRIMARY KEY,
    hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
    feature_keys TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('demo','live')),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intelligence.audit_events (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    hotel_id TEXT,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
