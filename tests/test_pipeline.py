import httpx
import json
import pytest
from fastapi.testclient import TestClient
from intelligence.api import app
from intelligence.connectors import OverpassConnector, Source, WikidataConnector
from intelligence.engine import create_job, plan, run_job
from intelligence.geometry import haversine_m, line_distance_m
from intelligence.hotelier import extract
from intelligence.registry import Registry
from intelligence.search import parse_query
from intelligence.store import get_evidence, get_profile, put_assertion, put_source, session, stats


def test_geometry():
    assert haversine_m(43.57, 39.73, 43.57, 39.73) == 0
    assert 100 < line_distance_m(43.57, 39.73, [[[43.571, 39.72], [43.571, 39.74]]]) < 120
    with pytest.raises(ValueError):
        line_distance_m(43.57, 39.73, [])


def test_registry_expansion(tmp_path):
    path = tmp_path / 'features.yaml'
    path.write_text('features:\n- key: e_bike_rental\n  version: 1\n  data_type: boolean\n  scope: hotel\n  strategy: hotelier\n')
    r = Registry(path)
    assert r.validate('e_bike_rental', True)
    with pytest.raises(ValueError):
        r.validate('e_bike_rental', 'true')


def test_overpass_response():
    def transport(request):
        assert request.method == 'POST'
        assert 'natural' in request.content.decode()
        return httpx.Response(200, json={'elements': [{'type': 'way', 'id': 12, 'geometry': [
            {'lat': 43.570, 'lon': 39.729}, {'lat': 43.570, 'lon': 39.734}]}]})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        source = OverpassConnector(client=client).fetch_coastlines(43.571, 39.731)
    assert source.provenance == 'live'
    assert source.license == 'ODbL'
    assert source.payload['osm_way_ids'] == ['12']
    assert 100 < line_distance_m(43.571, 39.731, source.payload['lines']) < 120


def test_wikidata_response():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
         'results': {'bindings': [{'inception': {'value': '1973-01-01T00:00:00Z'}}]}}))) as client:
        source = WikidataConnector(client=client).fetch_building_year('Q123')
    assert source.payload['years'] == [1973]
    with pytest.raises(ValueError):
        WikidataConnector().fetch_building_year('incorrect')


def test_vertical_pipeline(isolated_db):
    path, hotel_id = isolated_db
    with session(path) as db:
        job, created = create_job(db, hotel_id, ['coast_straight_m', 'dogs_allowed'],
                                  'demo', 'key1', Registry())
        again, duplicate = create_job(db, hotel_id, ['coast_straight_m', 'dogs_allowed'],
                                      'demo', 'key1', Registry())
        assert created and not duplicate and job == again
        with pytest.raises(ValueError):
            create_job(db, hotel_id, ['building_year'], 'demo', 'key1', Registry())
    run_job(job, path)
    with session(path) as db:
        profile = get_profile(db, hotel_id, Registry())
        distance = profile['facts']['coast_straight_m']
        assert distance['status'] == 'estimated' and distance['value'] > 0
        assert profile['facts']['dogs_allowed']['status'] == 'unknown'
        evidence = get_evidence(db, hotel_id, 'coast_straight_m')
        assert len(evidence) == 1
        assert evidence[0]['provenance'] == 'synthetic_fixture'
        assert evidence[0]['algorithm_version'] == 'geo.shortest_distance_to_coast_v1'
        assert len(evidence[0]['input_source_ids']) == 2
        assert stats(db, Registry())['coverage'] > 0
    run_job(job, path)
    with session(path) as db:
        assert len(get_evidence(db, hotel_id, 'coast_straight_m')) == 1


def test_conflict_and_non_synthetic_priority(isolated_db):
    path, hotel_id = isolated_db
    registry = Registry()
    with session(path) as db:
        first = Source('hotelier', 'first', 'urn:hotel:first', {}, '2026-10-26', 'hotelier_confirmed')
        second = Source('hotelier', 'second', 'urn:hotel:second', {}, '2026-10-26', 'hotelier_confirmed')
        a, b = put_source(db, first), put_source(db, second)
        put_assertion(db, registry, hotel_id, 'dogs_allowed', True, a, 'hotelier_reported', 0.8)
        put_assertion(db, registry, hotel_id, 'dogs_allowed', False, b, 'hotelier_reported', 0.95)
        fact = get_profile(db, hotel_id, registry)['facts']['dogs_allowed']
        assert fact['status'] == 'conflicted' and fact['value'] is None
        assert len(fact['assertion_ids']) == 2
        assert stats(db, registry)['conflicts'] == 1


def test_extraction():
    parsed = extract('С собаками можно до 12 кг, доплата 1500 рублей за ночь. С кошками нельзя.')
    found = {key: value for key, value, _ in parsed}
    assert found['dogs_allowed'] is True
    assert found['cats_allowed'] is False
    assert found['pet_max_weight_kg'] == 12
    assert found['pet_fee'] == {'amount': 1500, 'currency': 'RUB', 'per': 'night'}


def test_query():
    criteria = parse_query('У моря с собакой 12 кг и видом на закат')
    assert {x['feature_key'] for x in criteria} == {
        'coast_straight_m', 'dogs_allowed', 'pet_max_weight_kg', 'sunset_view'}


def test_api_and_hotelier_flow(isolated_db):
    path, hotel_id = isolated_db
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert len(client.get('/api/v2/intelligence/features').json()['features']) == 17
        request = {'hotel_id': hotel_id, 'feature_keys': ['coast_straight_m', 'building_year'], 'mode': 'demo'}
        response = client.post('/api/v2/intelligence/jobs/enrich', json=request,
                               headers={'Idempotency-Key': 'test-key-1'})
        assert response.status_code == 202, response.text
        job_id = response.json()['job_id']
        job = client.get(f'/api/v2/intelligence/jobs/{job_id}').json()
        assert job['status'] == 'partial'
        profile = client.get(f'/api/v2/intelligence/hotels/{hotel_id}/profile').json()
        coast = next(x for x in profile['facts'] if x['feature_key'] == 'coast_straight_m')
        assert coast['status'] == 'estimated'
        question = client.post('/api/v2/intelligence/hotelier/questions', json={
            'hotel_id': hotel_id, 'feature_keys': ['dogs_allowed', 'pet_max_weight_kg', 'pet_fee', 'cats_allowed'],
            'question': 'Принимаете собак, кошек, какая цена и ограничения по весу?', 'reason': 'unknown'})
        assert question.status_code == 201
        question_id = question.json()['question_id']
        response = client.post(f'/api/v2/intelligence/hotelier/questions/{question_id}/answer',
                               json={'text': 'С собаками можно до 12 кг, доплата 1500 рублей за ночь. С кошками нельзя.'})
        assert response.status_code == 200, response.text
        answers = response.json()
        assert len(answers['candidates']) == 4
        profile_before = client.get(f'/api/v2/intelligence/hotels/{hotel_id}/profile').json()
        assert next(x for x in profile_before['facts'] if x['feature_key'] == 'dogs_allowed')['status'] == 'unknown'
        response = client.post(f"/api/v2/intelligence/hotelier/answers/{answers['answer_id']}/confirm",
                               json={'candidate_ids': [c['id'] for c in answers['candidates']]})
        assert response.status_code == 200, response.text
        profile_after = client.get(f'/api/v2/intelligence/hotels/{hotel_id}/profile').json()
        assert next(x for x in profile_after['facts'] if x['feature_key'] == 'dogs_allowed')['status'] == 'reported'
        search = client.post('/api/v2/intelligence/search', json={'query': 'отель с собакой 12 кг рядом с морем'}).json()
        assert search['results'][0]['eligible'] is True
        assert len(search['results'][0]['matches']) == 3
        metrics = client.get('/api/v2/intelligence/metrics').json()
        assert metrics['precision'] is None
        assert metrics['feature_coverage'] > 0
        evidence = client.get(f'/api/v2/intelligence/hotels/{hotel_id}/evidence?feature_key=pet_fee').json()
        assert evidence['assertions'][0]['provider'] == 'hotelier'


def test_live_requires_explicit_enable(isolated_db):
    _, hotel_id = isolated_db
    with TestClient(app) as client:
        response = client.post('/api/v2/intelligence/jobs/enrich', json={
            'hotel_id': hotel_id, 'feature_keys': ['coast_straight_m'], 'mode': 'live'},
            headers={'Idempotency-Key': 'blocked'})
        assert response.status_code == 403


def test_live_overpass_replaces_synthetic_without_false_conflict(isolated_db):
    path, hotel_id = isolated_db
    registry = Registry()
    class FakeOverpass:
        def fetch_coastlines(self, lat, lon):
            return Source('osm_overpass', 'way/457', 'https://www.openstreetmap.org/way/457',
                          {'lines': [[[lat + 0.003, lon - 0.002], [lat + 0.003, lon + 0.002]]],
                           'osm_way_ids': ['457']}, '2026-10-26T00:00:00Z', 'live', 'ODbL')
    with session(path) as db:
        demo_id, _ = create_job(db, hotel_id, ['coast_straight_m'], 'demo', 'demo-job', registry)
    run_job(demo_id, path)
    with session(path) as db:
        live_id, _ = create_job(db, hotel_id, ['coast_straight_m'], 'live', 'live-job', registry)
    run_job(live_id, path, overpass=FakeOverpass())
    with session(path) as db:
        fact = get_profile(db, hotel_id, registry)['facts']['coast_straight_m']
        assert fact['status'] == 'estimated'
        assert fact['value'] > 200
        assert len(fact['assertion_ids']) == 1
        assert len(get_evidence(db, hotel_id, 'coast_straight_m')) == 2


def test_source_failure_keeps_unknown(isolated_db):
    path, hotel_id = isolated_db
    class FailOverpass:
        def fetch_coastlines(self, lat, lon):
            raise httpx.TimeoutException('source timed out')
    with session(path) as db:
        job_id, _ = create_job(db, hotel_id, ['coast_straight_m'], 'live', 'timeout-job', Registry())
    steps = run_job(job_id, path, overpass=FailOverpass())
    assert steps[0]['state'] == 'failed'
    with session(path) as db:
        assert get_profile(db, hotel_id, Registry())['facts']['coast_straight_m']['status'] == 'unknown'


def test_production_rejects_anonymous_mutations(isolated_db, monkeypatch):
    _, hotel_id = isolated_db
    monkeypatch.setenv('OSTROVOK_MODE', 'production')
    monkeypatch.setenv('OSTROVOK_API_TOKEN', 'topsecret')
    with TestClient(app) as client:
        payload = {'hotel_id': hotel_id, 'feature_keys': ['dogs_allowed'],
                   'question': 'Какие условия для размещения с собакой?', 'reason': 'unknown'}
        assert client.post('/api/v2/intelligence/hotelier/questions', json=payload).status_code == 401
        assert client.post('/api/v2/intelligence/hotelier/questions', json=payload,
                           headers={'Authorization': 'Bearer topsecret'}).status_code == 201
        assert client.get('/api/v2/intelligence/hotelier/hotels/' + hotel_id + '/questions',
                          headers={'Authorization': 'Bearer topsecret'}).status_code == 403
