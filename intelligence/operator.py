import json

from .refresh import due_geo_tasks
from .registry import Registry


def overview(db):
    statuses = {row['status']: row['count'] for row in db.execute(
        'SELECT status, COUNT(*) AS count FROM urban_records GROUP BY status')}
    counts = {}
    for key, sql in {
        'urban_pending': "SELECT COUNT(*) AS n FROM urban_records WHERE status='pending'",
        'building_pending': "SELECT COUNT(*) AS n FROM building_identities WHERE status='pending'",
        'media_pending': "SELECT COUNT(*) AS n FROM media_candidates WHERE status='pending'",
        'geo_pending': "SELECT COUNT(*) AS n FROM geo_candidates WHERE status='pending'",
        'hotel_count': 'SELECT COUNT(*) AS n FROM hotels',
    }.items():
        counts[key] = db.execute(sql).fetchone()['n']
    return {'counts': counts, 'urban_statuses': statuses,
            'reviewed_not_verified': True,
            'note': 'A reviewed announcement does not prove actual on-site activity'}


def queue(db, limit=50):
    queries = {
        'urban': '''SELECT u.id,u.category AS kind,u.title,u.description,u.lat,u.lon,u.radius_m,
            s.url AS source_url,u.starts_on,u.ends_on,u.created_at FROM urban_records u
            JOIN source_records s ON s.id=u.source_id WHERE u.status='pending'
            ORDER BY u.created_at DESC LIMIT ?''',
        'building': '''SELECT b.hotel_id AS id,h.name AS title,b.qid AS kind,
            s.url AS source_url,b.reviewed_at AS created_at
            FROM building_identities b JOIN hotels h ON h.id=b.hotel_id
            JOIN source_records s ON s.id=b.source_id
            WHERE b.status='pending' ORDER BY h.name LIMIT ?''',
        'media': '''SELECT m.id,m.hotel_id,h.name AS hotel_name,m.title,
            s.url AS source_url,m.created_at FROM media_candidates m
            JOIN source_records s ON s.id=m.source_id JOIN hotels h ON h.id=m.hotel_id
            WHERE m.status='pending' ORDER BY m.created_at DESC LIMIT ?''',
        'geo': '''SELECT g.id,g.hotel_id,h.name AS title,g.osm_id AS kind,
            s.url AS source_url,g.lat,g.lon,g.score,g.created_at
            FROM geo_candidates g JOIN hotels h ON h.id=g.hotel_id
            JOIN source_records s ON s.id=g.source_id
            WHERE g.status='pending' ORDER BY g.score DESC LIMIT ?''',
    }
    return {key: [dict(row) for row in db.execute(query, (limit,))]
            for key, query in queries.items()}


def audit_log(db, limit=40):
    rows = db.execute('SELECT * FROM audit_events ORDER BY created_at DESC,id DESC LIMIT ?', (limit,))
    return [{'id': row['id'], 'event': row['action'], 'hotel_id': row['hotel_id'],
             'created_at': row['created_at'], 'detail': json.loads(row['detail_json'])}
            for row in rows]


def refresh_plan(db, limit=10):
    return {'live_executed': False,
            'tasks': due_geo_tasks(db, Registry(), limit=limit),
            'notice': 'This is a preview; no source queries have run'}
