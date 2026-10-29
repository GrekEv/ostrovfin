ALTER TABLE intelligence.urban_records
    DROP CONSTRAINT IF EXISTS urban_records_status_check;
ALTER TABLE intelligence.urban_records
    ADD CONSTRAINT urban_records_status_check
    CHECK (status IN ('pending','reviewed','rejected','superseded'));
CREATE INDEX IF NOT EXISTS idx_urban_source ON intelligence.urban_records(source_id);
