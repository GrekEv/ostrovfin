import json
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from intelligence.api import app
from intelligence.engine import create_job, run_job
from intelligence.registry import Registry
from intelligence.store import get_evidence, get_profile, initialize, list_hotels, session


def test_real_pilot_seed_and_provenance(tmp_path, monkeypatch):
    db_path = tmp_path / 'official.sqlite3'
    monkeypatch.setenv('OSTROVOK_INTELLIGENCE_DB', str(db_path))
    monkeypatch.setenv('OSTROVOK_MODE', 'demo')
    monkeypatch.setenv('OSTROVOK_ALLOW_LOCAL_WRITE', 'true')
    monkeypatch.setenv('OSTROVOK_DEMO_SEED', 'false')
    with TestClient(app) as client:
        hotels = client.get('/api/v2/intelligence/hotels').json()['hotels']
        assert len(hotels) == 5
        assert all(h['coord_status'] != 'synthetic_fixture' for h in hotels)
        assert len([h for h in hotels if h['lat'] is None]) == 4
        mercure = next(h for h in hotels if h['name'].startswith('Mercure'))
        radisson = next(h for h in hotels if h['name'].startswith('Radisson'))
        mercure_info = client.get(f"/api/v2/intelligence/hotels/{mercure['id']}/profile").json()
        facts = {f['feature_key']: f for f in mercure_info['facts']}
        assert facts['dogs_allowed']['value'] is True
        assert facts['pet_max_weight_kg']['value'] == 20
        assert facts['pet_fee']['value']['amount'] == 1000
        assert 'планировалось' in facts['renovation_notice']['value']
        evidence = client.get(f"/api/v2/intelligence/hotels/{mercure['id']}/evidence", params={'feature_key': 'dogs_allowed'}).json()['assertions']
        assert evidence[0]['provider'] == 'official_hotel_site'
        assert evidence[0]['provenance'] == 'curated_official_page'
        assert evidence[0]['source_note']
        assert evidence[0]['url'].startswith('https://accor.ru/')
        result = client.post('/api/v2/intelligence/search', json={'query': 'Отель с собакой 12 кг'}).json()
        mercure_match = next(x for x in result['results'] if x['hotel_id'] == mercure['id'])
        radisson_match = next(x for x in result['results'] if x['hotel_id'] == radisson['id'])
        assert mercure_match['eligible'] is True
        assert radisson_match['eligible'] is False
        assert any('ремонте' in warning for warning in mercure_match['caveats'])
        assert mercure_match['score'] == 100
        assert radisson_match['score'] < 100
    with session(db_path) as db:
        before = len(list_hotels(db))
    initialize(db_path)
    with session(db_path) as db:
        assert len(list_hotels(db)) == before


def test_no_coordinates_not_assumed(tmp_path, monkeypatch):
    path = tmp_path / 'geo.sqlite3'
    monkeypatch.setenv('OSTROVOK_INTELLIGENCE_DB', str(path))
    monkeypatch.setenv('OSTROVOK_DEMO_SEED', 'false')
    monkeypatch.setenv('OSTROVOK_ALLOW_LOCAL_WRITE', 'true')
    with TestClient(app) as client:
        hotel = next(h for h in client.get('/api/v2/intelligence/hotels').json()['hotels'] if h['name'].startswith('Pullman'))
        request = {'hotel_id': hotel['id'], 'feature_keys': ['coast_straight_m'], 'mode': 'demo'}
        response = client.post('/api/v2/intelligence/jobs/enrich',json=request,headers={'Idempotency-Key':'null-coordinate-test'})
        assert response.status_code == 202
        job = client.get('/api/v2/intelligence/jobs/'+response.json()['job_id']).json()
        assert any(step['state']=='needs_coordinates' for step in job['steps'])
        assert client.get(f"/api/v2/intelligence/hotels/{hotel['id']}/profile").json()['facts'][0]['value'] is None
        update = client.post(f"/api/v2/intelligence/hotels/{hotel['id']}/coordinates", json={
           'lat':43.58,'lon':39.74,'source_url':'https://www.openstreetmap.org/','source_reference':'test-osm-verified-node'})
        assert update.status_code == 200
        assert update.json()['coord_status'] == 'sourced_reviewed'
        get = client.get('/api/v2/intelligence/hotels').json()['hotels']
        assert next(h for h in get if h['id']==hotel['id'])['lat'] == 43.58


def test_expired_assertion_is_stale(tmp_path, monkeypatch):
    db_path=tmp_path/'freshness.sqlite3'
    monkeypatch.setenv('OSTROVOK_INTELLIGENCE_DB',str(db_path))
    monkeypatch.setenv('OSTROVOK_DEMO_SEED','false')
    with TestClient(app) as client:
        hotel=next(h for h in client.get('/api/v2/intelligence/hotels').json()['hotels'] if h['name'].startswith('Mercure'))
        with session(db_path) as db:
            observed=(datetime.now(timezone.utc)-timedelta(days=100)).isoformat()
            db.execute('UPDATE assertions SET observed_at=? WHERE hotel_id=? AND feature_key=?', (observed,hotel['id'],'dogs_allowed'))
        profile=client.get(f"/api/v2/intelligence/hotels/{hotel['id']}/profile").json()
        fact=next(f for f in profile['facts'] if f['feature_key']=='dogs_allowed')
        assert fact['status']=='stale'
        assert fact['expires_at'] is not None
        result=client.post('/api/v2/intelligence/search',json={'query':'Можно с собакой 12 кг'}).json()
        match=next(h for h in result['results'] if h['hotel_id']==hotel['id'])
        assert not match['eligible']


def test_synthetic_hotels_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv('OSTROVOK_INTELLIGENCE_DB',str(tmp_path/'demo.sqlite3'))
    monkeypatch.setenv('OSTROVOK_DEMO_SEED','true')
    with TestClient(app) as client:
        default=client.post('/api/v2/intelligence/search',json={'query':'Можно с собакой'}).json()
        shown=client.post('/api/v2/intelligence/search',json={'query':'Можно с собакой','include_demo':True}).json()
        assert all(not h['demo'] for h in default['results'])
        assert len(shown['results'])==len(default['results'])+3
