import json
from .connectors import OverpassConnector
from .store import initialize, list_hotels, session


def main():
    initialize()
    with session() as db:
        hotel = next((h for h in list_hotels(db) if h['lat'] is not None and h['lon'] is not None), None)
        if hotel is None:
            raise RuntimeError('No hotel with sourced coordinates')
    try:
        source = OverpassConnector().fetch_coastlines(hotel['lat'], hotel['lon'])
    except Exception as error:
        print(json.dumps({'source': 'osm_overpass', 'status': 'unavailable',
                          'error_type': type(error).__name__, 'reason': str(error)[:300]}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps({'source': 'osm_overpass', 'status': 'available',
                      'ways': len(source.payload['osm_way_ids']), 'retrieved_at': source.retrieved_at},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
