import json
from intelligence.database import postgres_sql, Record
from intelligence.geopostgis import geojson_multiline


def test_positional_parameters_and_upsert():
    assert postgres_sql('SELECT * FROM jobs WHERE id=?') == 'SELECT * FROM jobs WHERE id=%s'
    q = postgres_sql('INSERT OR IGNORE INTO jobs VALUES (?,?,?)')
    assert q == 'INSERT INTO jobs VALUES (%s,%s,%s) ON CONFLICT DO NOTHING'
    assert postgres_sql('INSERT INTO hotels VALUES (:id,:name)') == 'INSERT INTO hotels VALUES (%(id)s,%(name)s)'


def test_row_adapter():
    row = Record(id='1', status='queued')
    assert row['status'] == 'queued' and row[0] == '1' and dict(row)['id'] == '1'


def test_geojson_lon_lat_order():
    geom = json.loads(geojson_multiline([[[43.56, 39.73], [43.57, 39.74]]]))
    assert geom['type'] == 'MultiLineString'
    assert geom['coordinates'] == [[[39.73, 43.56], [39.74, 43.57]]]


def test_invalid_geojson_rejected():
    import pytest
    with pytest.raises(ValueError):
        geojson_multiline([[[91, 0], [43, 39]]])
    with pytest.raises(ValueError):
        geojson_multiline([])


def test_database_readiness(isolated_db):
    from fastapi.testclient import TestClient
    from intelligence.api import app
    with TestClient(app) as client:
        response = client.get('/ready')
        assert response.status_code == 200
        assert response.json()['storage'] == 'sqlite'
        assert response.json()['hotels'] >= 5
