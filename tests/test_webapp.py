from fastapi.testclient import TestClient
from intelligence.api import app


def test_miniapp_serves_client_and_assets():
    with TestClient(app) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert 'Найдём' not in page.text
        assert 'островок' in page.text
        assert 'data-tab="hotelier"' in page.text
        assert client.get('/app/app.js').status_code == 200
        assert client.get('/app/styles.css').status_code == 200


def test_search_and_profile_roundtrip():
    with TestClient(app) as client:
        result = client.post('/api/v2/intelligence/search', json={'query': 'тихий отель рядом с морем'})
        assert result.status_code == 200
        payload = result.json()
        assert 'results' in payload
        for hotel in payload['results']:
            p = client.get(f"/api/v2/intelligence/hotels/{hotel['hotel_id']}/profile")
            assert p.status_code == 200
            assert 'facts' in p.json()
