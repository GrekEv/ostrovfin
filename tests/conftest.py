import pytest
from intelligence.store import initialize, list_hotels, session


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    path = tmp_path / 'pilot.sqlite3'
    monkeypatch.setenv('OSTROVOK_INTELLIGENCE_DB', str(path))
    monkeypatch.setenv('OSTROVOK_MODE', 'demo')
    monkeypatch.setenv('OSTROVOK_ALLOW_LOCAL_WRITE', 'true')
    monkeypatch.delenv('OSTROVOK_API_TOKEN', raising=False)
    initialize(path)
    with session(path) as db:
        hotel_id = list_hotels(db)[0]['id']
    return path, hotel_id
