import hashlib
import math
from datetime import date

from .connectors import Source
from .store import audit, encode, iso_now, one, put_source

CATEGORIES = {'construction', 'road_closure', 'festival'}
STATUSES = {'pending', 'reviewed', 'rejected', 'superseded'}


def distance_m(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dp = phi2 - phi1
    dl = math.radians(lon2 - lon1)
    value = math.sin(dp / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return round(12742000 * math.asin(min(1, math.sqrt(value))), 1)


def submit_record(db, record):
    if record['category'] not in CATEGORIES:
        raise ValueError('Unsupported category')
    if date.fromisoformat(record['ends_on']) < date.fromisoformat(record['starts_on']):
        raise ValueError('Event end precedes start')
    if not record['source_url'].startswith('https://'):
        raise ValueError('HTTPS source required')
    timestamp = iso_now()
    source = Source(record['provider'], record['source_reference'], record['source_url'],
                    {'category': record['category'], 'title': record['title'],
                     'starts_on': record['starts_on'], 'ends_on': record['ends_on'],
                     'lat': record['lat'], 'lon': record['lon'], 'radius_m': record['radius_m'],
                     'note': record['description']}, timestamp, 'operator_submitted', 'not_assessed')
    source_id = put_source(db, source)
    identifier = hashlib.sha256(encode([source_id, record['category'], record['starts_on'],
                                        record['ends_on']]).encode()).hexdigest()[:32]
    db.execute('''INSERT OR IGNORE INTO urban_records
        (id,category,title,description,starts_on,ends_on,lat,lon,radius_m,source_id,status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (identifier, record['category'], record['title'], record['description'], record['starts_on'],
         record['ends_on'], record['lat'], record['lon'], record['radius_m'], source_id, 'pending', timestamp))
    audit(db, 'urban.submitted', None, {'id': identifier, 'source_id': source_id})
    return identifier


def review_record(db, record_id, status):
    if status not in {'reviewed', 'rejected'}:
        raise ValueError('Unsupported review status')
    result = db.execute('SELECT id,status,created_at FROM urban_records WHERE id=?', (record_id,)).fetchone()
    if result is None:
        raise ValueError('Unknown urban record')
    if result['status'] != 'pending':
        if result['status'] == status:
            return {'id': record_id, 'status': status}
        raise ValueError('Only pending records may be reviewed')
    revisions = db.execute('''SELECT u.id,u.status,u.created_at FROM urban_records u
        JOIN source_records s ON s.id=u.source_id
        WHERE s.provider=(SELECT s1.provider FROM urban_records u1 JOIN source_records s1
                          ON s1.id=u1.source_id WHERE u1.id=?)
          AND s.external_id=(SELECT s2.external_id FROM urban_records u2 JOIN source_records s2
                             ON s2.id=u2.source_id WHERE u2.id=?)
          AND u.id<>? ORDER BY u.created_at,u.id''' , (record_id, record_id, record_id)).fetchall()
    if status == 'reviewed':
        newer = [item for item in revisions if item['status'] == 'reviewed' and
                 (item['created_at'], item['id']) > (result['created_at'], record_id)]
        if newer:
            raise ValueError('A newer version is already published')
        for item in revisions:
            if item['status'] == 'reviewed':
                db.execute("UPDATE urban_records SET status='superseded' WHERE id=?", (item['id'],))
                audit(db, 'urban.superseded', None, {'id': item['id'], 'replaced_by': record_id})
    db.execute('UPDATE urban_records SET status=? WHERE id=?', (status, record_id))
    audit(db, 'urban.reviewed', None, {'id': record_id, 'previous': result['status'], 'status': status})
    return {'id': record_id, 'status': status}


def surroundings(db, hotel_id, arrival, departure, radius_m=750):
    hotel = one(db, hotel_id)
    if hotel is None:
        raise ValueError('Unknown hotel')
    start, finish = date.fromisoformat(arrival), date.fromisoformat(departure)
    if finish <= start or (finish - start).days > 60:
        raise ValueError('Invalid travel dates: checkout must be after checkin, maximum 60 nights')
    if not 100 <= radius_m <= 5000:
        raise ValueError('Invalid radius')
    if hotel['lat'] is None or hotel['lon'] is None or hotel['coord_status'] == 'synthetic_fixture':
        return {'hotel_id': hotel_id, 'status': 'unknown', 'events': [],
                'reason': 'Real sourced coordinates are required; missing location cannot prove safety'}
    rows = db.execute('''SELECT u.*,s.url,s.provider,s.retrieved_at FROM urban_records u
        JOIN source_records s ON s.id=u.source_id
        WHERE u.starts_on<? AND u.ends_on>=? AND u.status='reviewed'
        ORDER BY u.starts_on,u.id''', (departure, arrival)).fetchall()
    events = []
    for row in rows:
        dist = distance_m(hotel['lat'], hotel['lon'], row['lat'], row['lon'])
        if dist > radius_m + row['radius_m']:
            continue
        events.append({'id': row['id'], 'category': row['category'], 'title': row['title'],
                       'description': row['description'], 'starts_on': row['starts_on'],
                       'ends_on': row['ends_on'], 'distance_m': dist,
                       'source_url': row['url'], 'provider': row['provider'],
                       'retrieved_at': row['retrieved_at'], 'status': 'reported',
                       'warning': 'Scheduled/reported activity; actual onsite status not confirmed'})
    return {'hotel_id': hotel_id, 'status': 'reported' if events else 'unknown', 'events': events, 'arrival': arrival,
            'departure': departure, 'radius_m': radius_m,
            'reason': 'Reviewed records are not a comprehensive city register; no matches does not mean no activity'}


def pending_records(db, limit=50):
    rows = db.execute('''SELECT u.id,u.category,u.title,u.starts_on,u.ends_on,u.status,s.url
        FROM urban_records u JOIN source_records s ON s.id=u.source_id
        WHERE u.status='pending' ORDER BY u.created_at DESC LIMIT ?''', (limit,)).fetchall()
    return [dict(r) for r in rows]
