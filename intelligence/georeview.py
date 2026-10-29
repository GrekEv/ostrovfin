from .geoagent import candidate_id, candidate_source
from .store import audit, encode, one, put_source, iso_now
import json


def discover(db, hotel_id, finder):
    hotel = one(db, hotel_id)
    if hotel is None:
        raise ValueError('Unknown hotel')
    records = finder.find_hotels(hotel['name'], hotel['address'])
    for item in records:
        source_id = put_source(db, candidate_source(hotel_id, item))
        db.execute('''INSERT INTO geo_candidates
            (id,hotel_id,osm_id,lat,lon,score,source_id,tags_json,status,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO NOTHING''',
            (candidate_id(hotel_id, item['osm_id'], item['lat'], item['lon']), hotel_id, item['osm_id'], item['lat'],
             item['lon'], item['score'], source_id, encode(item['tags']), 'pending', iso_now()))
    audit(db, 'geo.candidates_discovered', hotel_id, {'count': len(records)})
    return list_candidates(db, hotel_id)


def list_candidates(db, hotel_id):
    if not one(db, hotel_id):
        raise ValueError('Unknown hotel')
    candidates = []
    for row in db.execute('SELECT * FROM geo_candidates WHERE hotel_id=? ORDER BY score DESC', (hotel_id,)):
        item = dict(row)
        item['tags'] = json.loads(item.pop('tags_json'))
        candidates.append(item)
    return candidates


def approve_candidate(db, hotel_id, candidate_key):
    row = db.execute('SELECT * FROM geo_candidates WHERE hotel_id=? AND id=?',
                     (hotel_id, candidate_key)).fetchone()
    if not row:
        raise ValueError('Unknown OSM candidate')
    db.execute('UPDATE geo_candidates SET status=? WHERE hotel_id=?', ('rejected', hotel_id))
    db.execute('UPDATE geo_candidates SET status=? WHERE id=?', ('approved', candidate_key))
    db.execute('UPDATE hotels SET lat=?,lon=?,coord_status=? WHERE id=?',
               (row['lat'], row['lon'], 'osm_manually_reviewed', hotel_id))
    db.execute("DELETE FROM resolved_facts WHERE hotel_id=? AND feature_key IN ('coast_straight_m','beach_route_m','beach_walk_minutes','dog_park_walk_minutes')", (hotel_id,))
    audit(db, 'geo.coordinates_approved', hotel_id, {'candidate_id': candidate_key, 'source_id': row['source_id'],
                                                     'lat': row['lat'], 'lon': row['lon']})
    return {'hotel_id': hotel_id, 'coord_status': 'osm_manually_reviewed', 'source_id': row['source_id'],
            'lat': row['lat'], 'lon': row['lon']}


def recent_changes(db, hotel_id, limit=40):
    return [{'event': row['action'], 'created_at': row['created_at'], 'details': json.loads(row['detail_json'])}
            for row in db.execute('SELECT * FROM audit_events WHERE hotel_id=? ORDER BY created_at DESC LIMIT ?',
                                  (hotel_id, limit))]
