import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from fastapi.testclient import TestClient
from intelligence.api import app
from intelligence.search import parse_query
from intelligence.store import get_profile, list_hotels, session
from intelligence.registry import Registry
from intelligence.telegram_auth import TelegramAuthError, verify_init_data


def signed_data(user_id, token='test-telegram-token', age=0):
    fields = {'auth_date': str(int(time.time()) - age),
              'user': json.dumps({'id': user_id, 'first_name': 'Partner'}, separators=(',', ':'))}
    check = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_telegram_signature_and_expiration():
    signed = signed_data(7654321)
    assert verify_init_data(signed, 'test-telegram-token')['id'] == 7654321
    try:
        verify_init_data(signed.replace('Partner', 'Attacker'), 'test-telegram-token')
        assert False, 'Tampering must fail'
    except TelegramAuthError:
        pass
    try:
        verify_init_data(signed_data(7654321, age=90000), 'test-telegram-token')
        assert False, 'Expired signature must fail'
    except TelegramAuthError:
        pass


def test_walk_distance_not_substituted_for_direct():
    criteria = parse_query('До пляжа 10 минут пешком, с собакой 12 кг')
    keys = {item['feature_key'] for item in criteria}
    assert 'beach_walk_minutes' in keys
    assert 'coast_straight_m' not in keys
    assert next(x for x in criteria if x['feature_key'] == 'beach_walk_minutes')['value'] == 10


def test_unmatched_query_has_no_fake_score(isolated_db):
    with TestClient(app) as client:
        result = client.post('/api/v2/intelligence/search', json={'query': 'Любой отель'}).json()
        assert not result['criteria']
        assert all(item['score'] is None and not item['eligible'] for item in result['results'])


def test_telegram_hotelier_only_sees_assigned_hotel(isolated_db, monkeypatch):
    _, hotel_id = isolated_db
    monkeypatch.setenv('OSTROVOK_MODE', 'production')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-telegram-token')
    monkeypatch.setenv('OSTROVOK_API_TOKEN', 'internal-only')
    monkeypatch.setenv('OSTROVOK_HOTELIER_BINDINGS_JSON', json.dumps({'7654321': hotel_id}))
    with TestClient(app) as client:
        headers={'Authorization':'tma '+signed_data(7654321)}
        assert client.get('/api/v2/intelligence/me', headers=headers).json()['hotel_id'] == hotel_id
        assert client.get('/api/v2/intelligence/hotelier/hotels/'+hotel_id+'/questions', headers=headers).status_code == 200
        other='7914497a-b266-5276-bc74-b2be12a96e12'
        assert client.get('/api/v2/intelligence/hotelier/hotels/'+other+'/questions', headers=headers).status_code == 403
        bad={'Authorization':'tma '+signed_data(444)}
        assert client.get('/api/v2/intelligence/hotelier/hotels/'+hotel_id+'/questions', headers=bad).status_code == 403
        assert client.get('/api/v2/intelligence/hotelier/hotels/'+hotel_id+'/questions',headers={'Authorization':'Bearer internal-only','X-Actor-Hotel-Id':hotel_id}).status_code == 403
        assert client.post('/api/v2/intelligence/hotelier/questions',json={'hotel_id':hotel_id,'feature_keys':['dogs_allowed'],'question':'Разрешены ли собаки?','reason':'unknown'},headers=headers).status_code == 401


def test_demo_hotels_have_no_real_provenance(isolated_db):
    path,_=isolated_db
    with session(path) as db:
        assert len(list_hotels(db)) == 1


def test_servable_new_client():
    with TestClient(app) as client:
        assert 'Избранное' in client.get('/').text
        assert 'initData' in client.get('/app/app.js').text
        assert client.get('/api/v2/intelligence/me').status_code == 200
