CREATE TABLE IF NOT EXISTS intelligence.urban_records (
 id TEXT PRIMARY KEY,
 category TEXT NOT NULL CHECK (category IN ('construction','road_closure','festival')),
 title TEXT NOT NULL,
 description TEXT NOT NULL,
 starts_on TEXT NOT NULL,
 ends_on TEXT NOT NULL,
 lat DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
 lon DOUBLE PRECISION NOT NULL CHECK (lon BETWEEN -180 AND 180),
 radius_m DOUBLE PRECISION NOT NULL CHECK (radius_m >= 0 AND radius_m <= 2000),
 source_id TEXT NOT NULL REFERENCES intelligence.source_records(id),
 status TEXT NOT NULL CHECK (status IN ('pending','reviewed','rejected')),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_urban_records_date ON intelligence.urban_records(starts_on,ends_on);
CREATE TABLE IF NOT EXISTS intelligence.building_identities (
 hotel_id TEXT PRIMARY KEY REFERENCES intelligence.hotels(id),
 qid TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES intelligence.source_records(id),
 status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected')),
 reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS intelligence.media_candidates (
 id TEXT PRIMARY KEY,
 hotel_id TEXT NOT NULL REFERENCES intelligence.hotels(id),
 title TEXT NOT NULL,
 lat DOUBLE PRECISION NOT NULL,
 lon DOUBLE PRECISION NOT NULL,
 distance_m DOUBLE PRECISION NOT NULL,
 source_id TEXT NOT NULL REFERENCES intelligence.source_records(id),
 status TEXT NOT NULL CHECK (status IN ('pending','reviewed','rejected')),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_candidates_hotel ON intelligence.media_candidates(hotel_id);
