CREATE TABLE IF NOT EXISTS intelligence.geo_candidates (
    id TEXT PRIMARY KEY,
    hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
    osm_id TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL CHECK(lat BETWEEN -90 AND 90),
    lon DOUBLE PRECISION NOT NULL CHECK(lon BETWEEN -180 AND 180),
    score DOUBLE PRECISION NOT NULL CHECK(score BETWEEN 0 AND 1),
    source_id TEXT NOT NULL REFERENCES intelligence.source_records(id),
    tags_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
    created_at TEXT NOT NULL,
    UNIQUE(hotel_id,osm_id,lat,lon)
);
CREATE INDEX IF NOT EXISTS idx_intelligence_geo_candidates_hotel
    ON intelligence.geo_candidates(hotel_id);
