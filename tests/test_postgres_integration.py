import os
import uuid
import pytest


@pytest.mark.skipif(not os.getenv('OSTROVOK_TEST_POSTGRES_DSN'), reason='Disposable PostGIS test database not configured')
def test_real_postgis_pipeline(monkeypatch):
    from intelligence.database import migrate
    from intelligence.engine import create_job, run_job
    from intelligence.registry import Registry
    from intelligence.store import initialize, get_profile, list_hotels, session

    monkeypatch.setenv('OSTROVOK_STORAGE', 'postgres')
    monkeypatch.setenv('OSTROVOK_DATABASE_URL', os.environ['OSTROVOK_TEST_POSTGRES_DSN'])
    monkeypatch.delenv('OSTROVOK_AUTO_MIGRATE', raising=False)
    migrate()
    initialize()
    with session() as db:
        hotel_id = list_hotels(db)[0]['id']
        job_id, created = create_job(db, hotel_id, ['coast_straight_m'], 'demo', 'integration-' + str(uuid.uuid4()), Registry())
        assert created
    run_job(job_id)
    with session() as db:
        result = get_profile(db, hotel_id, Registry())['facts']['coast_straight_m']
        geometries = db.execute("SELECT count(*) FROM geo_features WHERE category='coastline'").fetchone()[0]
        assert result['value'] > 0 and result['status'] == 'estimated'
        assert geometries > 0
        assert db.execute('SELECT PostGIS_Version() AS v').fetchone()['v']
