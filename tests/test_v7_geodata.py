import httpx
import json
from urllib.parse import parse_qs

from fastapi.testclient import TestClient

from intelligence.api import app
from intelligence.connectors import Source
from intelligence.engine import create_job, run_job
from intelligence.geoagent import OverpassPlaces, ValhallaPedestrian, candidate_id, hotel_match_score
from intelligence.georeview import approve_candidate, discover, recent_changes
from intelligence.registry import Registry
from intelligence.store import get_evidence, get_profile, one, session


def fake_osm(request):
    query = parse_qs(request.content.decode())['data'][0]
    if '"tourism"="hotel"' in query:
        return httpx.Response(200, json={'elements': [
            {'type': 'node', 'id': 101, 'lat': 43.5701, 'lon': 39.7322,
             'tags': {'tourism': 'hotel', 'name': 'Гранд отель Жемчужина', 'addr:housenumber': '3'}},
            {'type': 'node', 'id': 102, 'lat': 43.57, 'lon': 39.73,
             'tags': {'tourism': 'hotel', 'name': 'Совсем другой отель'}}]})
    if '"natural"="beach"' in query:
        return httpx.Response(200, json={'elements': [
            {'type': 'way', 'id': 501, 'center': {'lat': 43.567, 'lon': 39.731},
             'tags': {'natural': 'beach', 'name': 'Пляж Альфа'}},
            {'type': 'node', 'id': 502, 'lat': 43.569, 'lon': 39.731,
             'tags': {'natural': 'beach', 'name': 'Закрытый пляж', 'access': 'private'}},
            {'type': 'node', 'id': 503, 'lat': 43.565, 'lon': 39.734,
             'tags': {'natural': 'beach', 'name': 'Пляж Бета', 'access': 'yes'}}]})
    return httpx.Response(500)


def fake_valhalla(request):
    assert request.method == 'POST'
    body = json.loads(request.content)
    assert body['costing'] == 'pedestrian'
    assert body['units'] == 'km'
    return httpx.Response(200, json={'trip': {'summary': {'length': 0.76, 'time': 622}}})


def test_match_and_beach_candidates():
    assert hotel_match_score('Гранд отель Жемчужина', 'Сочи, Черноморская 3',
                             {'name': 'Гранд отель Жемчужина', 'addr:housenumber': '3'}) >= .95
    with httpx.Client(transport=httpx.MockTransport(fake_osm)) as client:
        provider = OverpassPlaces(client=client)
        matches = provider.find_hotels('Гранд отель Жемчужина', 'Сочи, Черноморская 3')
        assert len(matches) == 1
        assert matches[0]['osm_id'] == 'node/101'
        beaches = provider.find_beaches(43.570, 39.732)
    assert len(beaches) == 2
    assert not any(b['osm_id'] == 'node/502' for b in beaches)
    assert all(b['point_kind'] in ('mapped_node', 'geometry_center') for b in beaches)


def test_routing_validation():
    with httpx.Client(transport=httpx.MockTransport(fake_valhalla)) as client:
        route = ValhallaPedestrian(client=client).route((43.57, 39.73), (43.567, 39.731))
    assert route['distance_m'] == 760
    assert route['duration_minutes'] == 10.4
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'trip': {}}))) as client:
        try:
            ValhallaPedestrian(client=client).route((43.57, 39.73), (43.567, 39.731))
            assert False, 'must reject missing route'
        except ValueError:
            pass


def test_coordinate_review_and_route_pipeline(isolated_db):
    path, hotel_id = isolated_db
    with httpx.Client(transport=httpx.MockTransport(fake_osm)) as client:
        provider = OverpassPlaces(client=client)
        with session(path) as db:
            candidates = discover(db, hotel_id, provider)
            assert len(candidates) == 1
            assert one(db, hotel_id)['coord_status'] == 'third_party_reported'
            approved = approve_candidate(db, hotel_id, candidates[0]['id'])
            assert approved['coord_status'] == 'osm_manually_reviewed'
        with httpx.Client(transport=httpx.MockTransport(fake_valhalla)) as route_client:
            with session(path) as db:
                job_id, _ = create_job(db, hotel_id, ['beach_route_m', 'beach_walk_minutes'],
                                       'live', 'geo-pipeline-v7', Registry())
            results = run_job(job_id, path, places=provider, router=ValhallaPedestrian(client=route_client))
    assert len(results) == 2
    assert all(item['state'] == 'completed' and item['access_verified'] is False for item in results)
    with session(path) as db:
        profile = get_profile(db, hotel_id, Registry())
        assert profile['facts']['beach_route_m']['value'] == 760
        assert profile['facts']['beach_walk_minutes']['value'] == 10.4
        assert profile['facts']['beach_walk_minutes']['status'] == 'estimated'
        evidence = get_evidence(db, hotel_id, 'beach_walk_minutes')
        assert len(evidence) == 1
        assert len(evidence[0]['input_source_ids']) == 3
        assert evidence[0]['source_note'] is None
        assert recent_changes(db, hotel_id)[0]['event'] == 'enrichment.completed'


def test_route_update_supersedes_not_conflicts(isolated_db):
    path, hotel_id = isolated_db
    class Beaches:
        def find_beaches(self, lat, lon):
            return [{'osm_id': 'node/123', 'lat': lat - .004, 'lon': lon, 'name': 'Test',
                     'url': 'https://www.openstreetmap.org/node/123', 'straight_m': 444,
                     'point_kind': 'mapped_node', 'access': 'unknown'}]
    class Route:
        minutes = 8
        def route(self, a, b):
            return {'distance_m': 540 + 50 * self.minutes,
                    'duration_minutes': self.minutes, 'duration_seconds': self.minutes * 60,
                    'origin': list(a), 'destination': list(b), 'costing': 'pedestrian'}
    router = Route()
    with session(path) as db:
        first, _ = create_job(db, hotel_id, ['beach_walk_minutes'], 'live', 'refresh-1', Registry())
    run_job(first, path, places=Beaches(), router=router)
    router.minutes = 11
    with session(path) as db:
        second, _ = create_job(db, hotel_id, ['beach_walk_minutes'], 'live', 'refresh-2', Registry())
    run_job(second, path, places=Beaches(), router=router)
    with session(path) as db:
        profile = get_profile(db, hotel_id, Registry())['facts']['beach_walk_minutes']
        assert profile['value'] == 11
        assert profile['status'] == 'estimated'
        assert len(profile['assertion_ids']) == 1
        assert len(get_evidence(db, hotel_id, 'beach_walk_minutes')) == 2
        changes = recent_changes(db, hotel_id)
        assert any(c['event'] == 'fact.changed' and c['details']['old_value'] == 8
                   and c['details']['new_value'] == 11 for c in changes)


def test_geo_api_local_auth_and_offline(isolated_db, monkeypatch):
    _, hotel_id = isolated_db
    monkeypatch.setenv('OSTROVOK_LIVE_SOURCES', 'false')
    with TestClient(app) as client:
        response = client.post(f'/api/v2/intelligence/hotels/{hotel_id}/geo/discover')
        assert response.status_code == 403
        result = client.get(f'/api/v2/intelligence/hotels/{hotel_id}/geo/status')
        assert result.status_code == 200
        assert result.json()['facts']['beach_walk_minutes']['status'] == 'unknown'
        assert client.post(f'/api/v2/intelligence/hotels/{hotel_id}/geo/approve',
                           json={'candidate_id': '0' * 32}).status_code == 404
    monkeypatch.setenv('OSTROVOK_MODE', 'production')
    monkeypatch.setenv('OSTROVOK_API_TOKEN', 'test-secret')
    with TestClient(app) as client:
        assert client.get(f'/api/v2/intelligence/hotels/{hotel_id}/geo/candidates').status_code == 401
        assert client.get(f'/api/v2/intelligence/hotels/{hotel_id}/changes').status_code == 401


def test_refresh_due_and_stale_after_success(isolated_db):
    from intelligence.refresh import due_geo_tasks
    path, hotel_id = isolated_db
    with session(path) as db:
        tasks = due_geo_tasks(db, Registry(), limit=5)
        assert tasks and hotel_id in [task['hotel_id'] for task in tasks]
    class Beaches:
        def find_beaches(self, lat, lon):
            return [{'osm_id': 'node/12', 'lat': lat - .004, 'lon': lon, 'name': 'Beach',
                     'url': 'https://www.openstreetmap.org/node/12', 'straight_m': 400, 'access': 'yes'}]
    class Route:
        def route(self, a, b):
            return {'distance_m': 650, 'duration_minutes': 8.5, 'duration_seconds': 510,
                    'origin': list(a), 'destination': list(b), 'costing': 'pedestrian'}
    with session(path) as db:
        job, _ = create_job(db, hotel_id, ['beach_route_m','beach_walk_minutes'], 'live',
                            'due-check', Registry())
    run_job(job, path, places=Beaches(), router=Route())
    with session(path) as db:
        due = due_geo_tasks(db, Registry(), limit=5)
        task = next(t for t in due if t['hotel_id'] == hotel_id)
        assert 'beach_walk_minutes' not in task['feature_keys']
        assert 'coast_straight_m' in task['feature_keys']


def test_recheck_identical_evidence_refreshes_date(isolated_db):
    path, hotel_id = isolated_db
    registry = Registry()
    source = Source('routing_fixture', 'route-1', 'https://example.org/route',
                    {'length': 500}, '2026-10-25T00:00:00+00:00', 'live', 'ODbL')
    with session(path) as db:
        from intelligence.store import put_assertion, put_source
        id1 = put_source(db, source)
        assertion = put_assertion(db, registry, hotel_id, 'beach_route_m', 500, id1,
                                  'derived', .7, observed_at='2026-10-25T00:00:00+00:00')
        again = Source(source.provider, source.external_id, source.url, source.payload,
                       '2026-10-26T00:00:00+00:00', source.provenance, source.license)
        id2 = put_source(db, again)
        assert id1 == id2
        put_assertion(db, registry, hotel_id, 'beach_route_m', 500, id2,
                      'derived', .7, observed_at='2026-10-26T00:00:00+00:00')
        record = db.execute('SELECT observed_at FROM assertions WHERE id=?', (assertion,)).fetchone()
        assert record['observed_at'] == '2026-10-26T00:00:00+00:00'
        record = db.execute('SELECT retrieved_at FROM source_records WHERE id=?', (id1,)).fetchone()
        assert record['retrieved_at'] == '2026-10-26T00:00:00+00:00'


def test_overpass_partial_response_rejected():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        'elements': [], 'remark': 'runtime error: query timed out'}))) as client:
        try:
            OverpassPlaces(client=client).find_beaches(43.57, 39.73)
            assert False
        except ValueError as error:
            assert 'partial' in str(error)


def test_geo_discover_api_with_mock_provider(isolated_db, monkeypatch):
    _, hotel_id = isolated_db
    import intelligence.api as routes
    with httpx.Client(transport=httpx.MockTransport(fake_osm)) as source_client:
        monkeypatch.setattr(routes, 'OverpassPlaces', lambda *a, **kw: OverpassPlaces(client=source_client))
        monkeypatch.setenv('OSTROVOK_LIVE_SOURCES', 'true')
        with TestClient(app) as client:
            result = client.post(f'/api/v2/intelligence/hotels/{hotel_id}/geo/discover')
            assert result.status_code == 200
            candidate = result.json()['candidates'][0]
            assert candidate['score'] > .5
            assert result.json()['needs_manual_review'] is True
            assert client.get(f'/api/v2/intelligence/hotels/{hotel_id}/geo/candidates').json()['candidates']
            chosen = client.post(f'/api/v2/intelligence/hotels/{hotel_id}/geo/approve',
                                 json={'candidate_id': candidate['id']})
            assert chosen.status_code == 200
            status = client.get(f'/api/v2/intelligence/hotels/{hotel_id}/geo/status').json()
            assert status['coordinate_status'] == 'osm_manually_reviewed'
            history = client.get(f'/api/v2/intelligence/hotels/{hotel_id}/changes').json()
            assert any(item['event'] == 'geo.coordinates_approved' for item in history['changes'])


def test_coordinate_drift_needs_new_approval(isolated_db):
    path, hotel_id = isolated_db
    class Finder:
        lat = 43.5701
        def find_hotels(self, *args):
            return [{'osm_id': 'node/44', 'lat': self.lat, 'lon': 39.7322,
                     'name': 'Гранд отель Жемчужина', 'score': .98,
                     'tags': {'name': 'Гранд отель Жемчужина'},
                     'url': 'https://www.openstreetmap.org/node/44'}]
    finder = Finder()
    with session(path) as db:
        first = discover(db, hotel_id, finder)
        approved = approve_candidate(db, hotel_id, first[0]['id'])
        assert approved['lat'] == 43.5701
        finder.lat = 43.5711
        second = discover(db, hotel_id, finder)
        assert len(second) == 2
        assert sum(item['status'] == 'approved' for item in second) == 1
        assert sum(item['status'] == 'pending' for item in second) == 1
        assert one(db, hotel_id)['lat'] == 43.5701


def test_untraceable_coordinates_not_used_for_live(isolated_db):
    path, hotel_id = isolated_db
    with session(path) as db:
        db.execute('DELETE FROM source_records WHERE id NOT IN (SELECT source_id FROM assertions)')
        db.execute("UPDATE hotels SET coord_status='sourced_reviewed',lat=43.578,lon=39.733 WHERE id=?", (hotel_id,))
        job, _ = create_job(db, hotel_id, ['beach_walk_minutes'], 'live', 'untraced-coord', Registry())
    class ShouldNotBeCalled:
        def find_beaches(self, *args):
            assert False, 'external service should not receive untraceable coordinates'
    steps = run_job(job, path, places=ShouldNotBeCalled())
    assert steps[0]['state'] == 'needs_coordinates'
