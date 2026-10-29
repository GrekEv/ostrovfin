import csv
import json

import pytest
from fastapi.testclient import TestClient

from intelligence.api import app
from intelligence.city_import import CityNotice, import_notices, load_notices
from intelligence.operator import audit_log, overview, queue, refresh_plan
from intelligence.store import session
from intelligence.urban import review_record, surroundings


ROOT = '/api/v2/intelligence'


def notice(**kwargs):
    row = {'provider': 'sochi_city_manual', 'source_reference': 'sochi-abc-100',
           'source_url': 'https://example.org/notice/100', 'category': 'construction',
           'title': 'Учебное уведомление о строительстве',
           'description': 'Демонстрация обработки городской записи и её редакций',
           'starts_on': '2026-11-05', 'ends_on': '2026-11-12',
           'lat': 43.575, 'lon': 39.723, 'radius_m': 200}
    row.update(kwargs)
    return CityNotice.model_validate(row).store_data()


def test_import_dry_run_and_idempotency(isolated_db):
    path, _ = isolated_db
    with session(path) as db:
        assert import_notices(db, [notice()], apply=False)['new'] == 1
        assert queue(db)['urban'] == []
        result = import_notices(db, [notice()], apply=True)
        assert result['new'] == 1
        assert len(queue(db)['urban']) == 1
        same = import_notices(db, [notice()], apply=True)
        assert same['unchanged'] == 1
        assert len(queue(db)['urban']) == 1
        assert overview(db)['counts']['urban_pending'] == 1
        assert audit_log(db)[0]['event'] == 'urban.submitted'


def test_city_revision_requires_approval(isolated_db):
    path, hotel_id = isolated_db
    with session(path) as db:
        from intelligence.store import one
        hotel=one(db, hotel_id)
        v1=notice(lat=hotel['lat'], lon=hotel['lon'])
        first=import_notices(db,[v1],apply=True)['items'][0]['id']
        assert surroundings(db,hotel_id,'2026-11-06','2026-11-07')['events']==[]
        review_record(db, first, 'reviewed')
        assert len(surroundings(db,hotel_id,'2026-11-06','2026-11-07')['events'])==1
        v2=notice(lat=hotel['lat'],lon=hotel['lon'],title='Стройка перенесена на другие даты',
                  starts_on='2026-12-06',ends_on='2026-12-09')
        report=import_notices(db,[v2],apply=True)
        assert report['revised']==1
        second=report['items'][0]['id']
        assert second != first
        assert len(surroundings(db,hotel_id,'2026-11-06','2026-11-07')['events'])==1
        review_record(db, second, 'reviewed')
        assert surroundings(db,hotel_id,'2026-11-06','2026-11-07')['events']==[]
        assert len(surroundings(db,hotel_id,'2026-12-07','2026-12-08')['events'])==1
        old=db.execute('SELECT status FROM urban_records WHERE id=?',(first,)).fetchone()
        assert old['status']=='superseded'
        assert any(x['event']=='urban.superseded' for x in audit_log(db))
        with pytest.raises(ValueError):
            review_record(db,first,'reviewed')


def test_rejected_revision_keeps_original(isolated_db):
    path, hotel_id = isolated_db
    with session(path) as db:
        from intelligence.store import one
        hotel=one(db,hotel_id)
        first=import_notices(db,[notice(lat=hotel['lat'],lon=hotel['lon'])],apply=True)['items'][0]['id']
        review_record(db,first,'reviewed')
        corrected=notice(lat=hotel['lat'],lon=hotel['lon'],title='Другая редакция уведомления')
        second=import_notices(db,[corrected],apply=True)['items'][0]['id']
        review_record(db,second,'rejected')
        assert db.execute('SELECT status FROM urban_records WHERE id=?',(first,)).fetchone()['status']=='reviewed'
        assert len(surroundings(db,hotel_id,'2026-11-06','2026-11-07')['events'])==1


def test_feed_validation_and_csv(tmp_path):
    f=tmp_path/'notices.csv'
    with f.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(notice()))
        writer.writeheader();writer.writerow(notice())
    assert load_notices(f)[0]['category']=='construction'
    with pytest.raises(ValueError):
        CityNotice.model_validate(notice(lat=60))
    with pytest.raises(ValueError):
        CityNotice.model_validate(notice(source_url='http://example.org/notice'))
    with pytest.raises(ValueError):
        CityNotice.model_validate(notice(starts_on='2026-11-15'))
    f2=tmp_path/'dup.json'
    f2.write_text(json.dumps({'records':[notice(),notice()]}),encoding='utf-8')
    with pytest.raises(ValueError,match='Duplicate'):
        load_notices(f2)


def test_operator_api_and_static_page(isolated_db,monkeypatch):
    _,hotel_id=isolated_db
    with TestClient(app) as client:
        assert client.get('/operator').status_code==200
        assert 'операторская панель' in client.get('/operator').text
        assert client.get('/app/operator.js').status_code==200
        for path in ('/operator/overview','/operator/queue','/operator/audit','/operator/refresh-plan'):
            assert client.get(ROOT+path).status_code==200
        result=client.get(ROOT+'/operator/refresh-plan').json()
        assert result['live_executed'] is False
    monkeypatch.setenv('OSTROVOK_MODE','production')
    monkeypatch.setenv('OSTROVOK_API_TOKEN','operator-test-secret')
    with TestClient(app) as client:
        assert client.get(ROOT+'/operator/queue').status_code==401
        assert client.get(ROOT+'/operator/queue',headers={'Authorization':'Bearer wrong'}).status_code==401
        good=client.get(ROOT+'/operator/queue',headers={'Authorization':'Bearer operator-test-secret'})
        assert good.status_code==200
        assert client.get(ROOT+'/operator/refresh-plan',headers={'Authorization':'Bearer operator-test-secret'}).status_code==200
