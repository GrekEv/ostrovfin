import httpx
from fastapi.testclient import TestClient

from intelligence.api import app
from intelligence.heritage import building_identity, review_identity, submit_identity
from intelligence.media import CommonsGeosearch, list_media, review_media, save_candidates
from intelligence.store import session
from intelligence.urban import pending_records, review_record, submit_record, surroundings


def event(**kwargs):
    result = {'category': 'construction', 'title': 'Ремонт набережной',
              'description': 'Работы на участке дороги согласно опубликованному уведомлению',
              'starts_on': '2026-11-01', 'ends_on': '2026-11-09',
              'lat': 43.575, 'lon': 39.723, 'radius_m': 120,
              'provider': 'municipal_manual', 'source_reference': 'case-001',
              'source_url': 'https://example.org/city/notice/001'}
    result.update(kwargs)
    return result


def test_city_event_window_and_noncoverage(isolated_db):
    path, hotel_id = isolated_db
    with session(path) as db:
        before = surroundings(db, hotel_id, '2026-11-02', '2026-11-06')
        assert before['events'] == []
        assert before['status'] == 'unknown'
        assert 'no matches does not mean' in before['reason']
        from intelligence.store import one
        hotel = one(db, hotel_id)
        record = event(lat=hotel['lat'], lon=hotel['lon'])
        record_id = submit_record(db, record)
        assert pending_records(db)[0]['id'] == record_id
        assert surroundings(db, hotel_id, '2026-11-03', '2026-11-05')['events'] == []
        review_record(db, record_id, 'reviewed')
        found = surroundings(db, hotel_id, '2026-11-03', '2026-11-05')
        assert len(found['events']) == 1
        assert found['events'][0]['distance_m'] == 0
        assert found['events'][0]['status'] == 'reported'
        assert surroundings(db, hotel_id, '2026-12-01', '2026-12-03')['events'] == []
        assert surroundings(db, hotel_id, '2026-11-09', '2026-11-10')['events']
        assert surroundings(db, hotel_id, '2026-11-10', '2026-11-11')['events'] == []


def test_city_validation_and_identity_review(isolated_db):
    path, hotel_id = isolated_db
    with session(path) as db:
        import pytest
        with pytest.raises(ValueError):
            submit_record(db, event(ends_on='2025-11-01'))
        with pytest.raises(ValueError):
            surroundings(db, hotel_id, '2026-11-10', '2026-11-01')
        with pytest.raises(ValueError):
            submit_identity(db, hotel_id, 'Qbad', 'https://example.org/a', 'Checked source')
        result = submit_identity(db, hotel_id, 'Q100', 'https://example.org/identity',
                                 'The address of the historic building matches')
        assert result['status'] == 'pending'
        assert building_identity(db, hotel_id)['status'] == 'pending'
        assert review_identity(db, hotel_id, True)['status'] == 'approved'
        assert building_identity(db, hotel_id)['qid'] == 'Q100'
        submit_identity(db, hotel_id, 'Q200', 'https://example.org/new', 'Different address requires review')
        assert building_identity(db, hotel_id)['status'] == 'pending'


def commons_fake(request):
    assert request.url.host == 'commons.wikimedia.org'
    assert request.url.params['ggsnamespace'] == '6'
    return httpx.Response(200, json={'query': {'pages': {
        '1': {'ns': 6, 'title': 'File:Facade.jpg', 'coordinates': [{'lat': 43.57, 'lon': 39.73}],
              'imageinfo': [{'mime': 'image/jpeg', 'thumburl': 'https://upload.wikimedia.org/a.jpg',
                             'extmetadata': {'LicenseShortName': {'value': 'CC BY-SA 4.0'}}}]},
        '2': {'ns': 6, 'title': 'File:Too far.jpg', 'coordinates': [{'lat': 49, 'lon': 39.73}],
              'imageinfo': [{'mime': 'image/jpeg', 'thumburl': 'https://upload.wikimedia.org/b.jpg'}]},
        '3': {'ns': 6, 'title': 'File:Wrong link.png', 'coordinates': [{'lat': 43.57, 'lon': 39.73}],
              'imageinfo': [{'mime': 'image/png', 'thumburl': 'https://bad.example/hijack.png'}]}
    }}})


def test_commons_geotags_stay_candidates(isolated_db):
    path, hotel_id = isolated_db
    with httpx.Client(transport=httpx.MockTransport(commons_fake)) as client:
        results = CommonsGeosearch(client=client).discover(43.57, 39.73)
    assert len(results) == 2
    assert results[0]['license'] == 'CC BY-SA 4.0'
    assert results[1]['thumbnail_url'] is None
    with session(path) as db:
        ids = save_candidates(db, hotel_id, results)
        assert len(ids) == 2
        assert list_media(db, hotel_id, public=True) == []
        reviewed = review_media(db, hotel_id, ids[0], True)
        assert reviewed['view_confirmed'] is False
        assert len(list_media(db, hotel_id, public=True)) == 1
        save_candidates(db, hotel_id, results)
        assert len(list_media(db, hotel_id)) == 2


def test_api_access_and_city_queries(isolated_db, monkeypatch):
    _, hotel_id = isolated_db
    root = '/api/v2/intelligence'
    with TestClient(app) as client:
        result = client.post(root + '/urban/records', json=event())
        assert result.status_code == 201
        assert result.json()['published'] is False
        item = result.json()['id']
        assert client.post(root + f'/urban/records/{item}/review', json={'status':'reviewed'}).status_code == 200
        summary = client.get(root + f'/hotels/{hotel_id}/surroundings',
                             params={'arrival': '2026-11-02', 'departure': '2026-11-03'})
        assert summary.status_code == 200
        assert summary.json()['status'] == 'unknown'
        assert client.get(root + f'/hotels/{hotel_id}/media').json()['view_confirmed'] is False
        assert client.post(root + f'/hotels/{hotel_id}/media/discover').status_code == 403
        assert client.post(root + f'/hotels/{hotel_id}/building/identity', json={
            'qid':'Q100', 'reference_url':'https://example.org/identity', 'note':'Verified same building address'}).status_code == 200
        assert client.get(root + f'/hotels/{hotel_id}/building/identity').json()['status'] == 'pending'
        assert client.post(root + f'/hotels/{hotel_id}/building/identity/review',
                           json={'approved': True}).json()['status'] == 'approved'
    monkeypatch.setenv('OSTROVOK_MODE', 'production')
    monkeypatch.setenv('OSTROVOK_API_TOKEN', 'v8-test-token')
    with TestClient(app) as client:
        assert client.post(root + '/urban/records', json=event()).status_code == 401
        assert client.get(root + '/urban/pending').status_code == 401
        assert client.post(root + f'/hotels/{hotel_id}/building/identity/review',
                           json={'approved': True}).status_code == 401
        assert client.get(root + f'/hotels/{hotel_id}/surroundings',
                          params={'arrival':'2026-11-02','departure':'2026-11-03'}).status_code == 200


def test_wikidata_inspector_never_fabricates_history(isolated_db):
    from intelligence.heritage import WikidataBuildingInspector, inspect_identity
    path, hotel_id = isolated_db
    def mock(request):
        assert request.url.params['ids'] == 'Q100'
        return httpx.Response(200, json={'entities': {'Q100': {
            'labels': {'ru': {'value': 'Здание у моря'}},
            'descriptions': {'en': {'value': 'historical building'}},
            'sitelinks': {'ruwiki': {'title': 'Историческое здание'}},
            'claims': {'P571': [{'mainsnak': {'datavalue': {'value': {'time': '+1905-01-01T00:00:00Z'}}}}]}
        }}})
    with httpx.Client(transport=httpx.MockTransport(mock)) as client:
        with session(path) as db:
            submit_identity(db, hotel_id, 'Q100', 'https://example.org/identity', 'Address checked with registry')
            result = inspect_identity(db, hotel_id, WikidataBuildingInspector(client=client))
            assert result['identity_status'] == 'pending'
            assert result['candidate']['inception_statements'] == ['+1905-01-01T00:00:00Z']
            assert result['building_history_fact_created'] is False


def test_search_date_scoped_urban_warning(isolated_db):
    path, hotel_id = isolated_db
    root='/api/v2/intelligence'
    with session(path) as db:
        from intelligence.store import one
        hotel=one(db, hotel_id)
        row=event(lat=hotel['lat'], lon=hotel['lon'], title='Тестовая стройка из примера')
        review_record(db, submit_record(db,row), 'reviewed')
    with TestClient(app) as client:
        data=client.post(root+'/search', json={'query':'рядом с морем',
                            'arrival':'2026-11-02','departure':'2026-11-03'}).json()
        matching=[x for x in data['results'] if x['hotel_id']==hotel_id]
        assert matching
        assert matching[0]['urban_context']['events'][0]['title']=='Тестовая стройка из примера'
        assert any('События поблизости' in x for x in matching[0]['caveats'])
        assert client.post(root+'/search',json={'query':'рядом с морем',
                          'arrival':'2026-11-05'}).status_code == 422
        assert client.post(root+'/search',json={'query':'рядом с морем',
                          'arrival':'2026-11-05','departure':'2026-10-05'}).status_code == 422
        assert client.post(root+'/search',json={'query':'рядом с морем',
                          'arrival':'2026-11-31','departure':'2026-12-02'}).status_code == 422


def test_construction_preference_never_assumes_safety(isolated_db):
    from intelligence.search import parse_query
    _, hotel_id = isolated_db
    criteria = parse_query('Хочу тихий отель без стройки')
    assert any(c['feature_key'] == 'active_construction_nearby' and c['value'] is False for c in criteria)
    root = '/api/v2/intelligence'
    with TestClient(app) as client:
        result = client.post(root+'/search', json={'query':'Хочу без стройки',
                     'arrival':'2026-11-12','departure':'2026-11-14'}).json()
        obj = next(x for x in result['results'] if x['hotel_id']==hotel_id)
        entry = next(x for x in obj['matches'] if x['feature_key']=='active_construction_nearby')
        assert entry['status'] == 'unknown'
        assert obj['eligible'] is False
