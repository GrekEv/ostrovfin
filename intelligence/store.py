import hashlib
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .registry import Registry
from .database import session, POSTGRES_MIGRATION, check_schema

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'intelligence-v8.sqlite3'


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


SCHEMA = '''
CREATE TABLE IF NOT EXISTS hotels (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL,
 lat REAL, lon REAL, coord_status TEXT NOT NULL,
 offer_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS source_records (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
 url TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
 retrieved_at TEXT NOT NULL, provenance TEXT NOT NULL, license TEXT NOT NULL,
 UNIQUE(provider, external_id, payload_sha256)
);
CREATE TABLE IF NOT EXISTS assertions (
 id TEXT PRIMARY KEY, hotel_id TEXT NOT NULL REFERENCES hotels(id),
 feature_key TEXT NOT NULL, value_json TEXT NOT NULL, unit TEXT,
 source_id TEXT NOT NULL REFERENCES source_records(id), method TEXT NOT NULL,
 confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 observed_at TEXT NOT NULL, scope TEXT NOT NULL, context_json TEXT NOT NULL,
 algorithm_version TEXT, input_source_ids TEXT NOT NULL,
 UNIQUE(hotel_id,feature_key,value_json,source_id,scope,context_json,algorithm_version)
);
CREATE TABLE IF NOT EXISTS resolved_facts (
 hotel_id TEXT NOT NULL REFERENCES hotels(id), feature_key TEXT NOT NULL,
 context_json TEXT NOT NULL, value_json TEXT, status TEXT NOT NULL,
 assertion_ids TEXT NOT NULL, rationale TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(hotel_id,feature_key,context_json)
);
CREATE TABLE IF NOT EXISTS questions (
 id TEXT PRIMARY KEY, hotel_id TEXT NOT NULL REFERENCES hotels(id),
 feature_keys TEXT NOT NULL, question TEXT NOT NULL, reason TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
 id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES questions(id),
 text TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
 id TEXT PRIMARY KEY, answer_id TEXT NOT NULL REFERENCES answers(id),
 feature_key TEXT NOT NULL, value_json TEXT NOT NULL, evidence_text TEXT NOT NULL,
 status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, hotel_id TEXT NOT NULL REFERENCES hotels(id),
 feature_keys TEXT NOT NULL, mode TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
 id TEXT PRIMARY KEY, action TEXT NOT NULL, hotel_id TEXT,
 detail_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS geo_candidates (
 id TEXT PRIMARY KEY, hotel_id TEXT NOT NULL REFERENCES hotels(id), osm_id TEXT NOT NULL,
 lat REAL NOT NULL, lon REAL NOT NULL, score REAL NOT NULL,
 source_id TEXT NOT NULL REFERENCES source_records(id), tags_json TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(hotel_id,osm_id,lat,lon)
);
CREATE TABLE IF NOT EXISTS urban_records (
 id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL,
 description TEXT NOT NULL, starts_on TEXT NOT NULL, ends_on TEXT NOT NULL,
 lat REAL NOT NULL, lon REAL NOT NULL, radius_m REAL NOT NULL,
 source_id TEXT NOT NULL REFERENCES source_records(id), status TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_urban_dates ON urban_records(starts_on,ends_on);
CREATE TABLE IF NOT EXISTS building_identities (
 hotel_id TEXT PRIMARY KEY REFERENCES hotels(id), qid TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES source_records(id), status TEXT NOT NULL,
 reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS media_candidates (
 id TEXT PRIMARY KEY, hotel_id TEXT NOT NULL REFERENCES hotels(id),
 title TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
 distance_m REAL NOT NULL, source_id TEXT NOT NULL REFERENCES source_records(id),
 status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_hotel ON media_candidates(hotel_id);
CREATE INDEX IF NOT EXISTS idx_geo_candidates_hotel ON geo_candidates(hotel_id);
CREATE INDEX IF NOT EXISTS idx_assertions_hotel_feature ON assertions(hotel_id,feature_key);
CREATE INDEX IF NOT EXISTS idx_sources_provider ON source_records(provider, external_id);
'''


def initialize(path=None):
    with session(path) as db:
        if path is None and os.getenv('OSTROVOK_STORAGE', 'sqlite').lower() == 'postgres':
            if os.getenv('OSTROVOK_AUTO_MIGRATE', 'false').lower() == 'true':
                db.executescript(POSTGRES_MIGRATION.read_text(encoding='utf-8'))
                db.executescript((ROOT / 'sql' / '003_geo_candidates.sql').read_text(encoding='utf-8'))
                db.executescript((ROOT / 'sql' / '004_research_v8.sql').read_text(encoding='utf-8'))
                db.executescript((ROOT / 'sql' / '005_operator_v9.sql').read_text(encoding='utf-8'))
            check_schema(db)
        else:
            db.executescript(SCHEMA)
        hotel = json.loads((ROOT / 'data' / 'pilot_hotel.json').read_text(encoding='utf-8'))
        db.execute('INSERT OR IGNORE INTO hotels(id,name,address,lat,lon,coord_status,offer_id) VALUES (:id,:name,:address,:lat,:lon,:coord_status,:offer_id)', hotel)
        if path is None and os.getenv('OSTROVOK_OFFICIAL_SEED', 'true').lower() == 'true':
            seed_official_hotels(db)
        if path is None and os.getenv('OSTROVOK_MODE', 'demo') == 'demo' and os.getenv('OSTROVOK_DEMO_SEED', 'false').lower() == 'true':
            seed_demo_hotels(db)



def seed_official_hotels(db):
    from .connectors import Source
    registry = Registry()
    records = json.loads((ROOT / 'data' / 'official_hotels.json').read_text(encoding='utf-8'))
    for item in records:
        db.execute('INSERT OR IGNORE INTO hotels(id,name,address,lat,lon,coord_status,offer_id) VALUES (?,?,?,?,?,?,?)',
                   (item['id'], item['name'], item['address'], item['lat'], item['lon'], item['coord_status'], None))
        source = Source('official_hotel_site', item['id'], item['url'],
                        {'hotel_id': item['id'], 'checked_at': item['source_checked_at'],
                         'curation': 'human-reviewed public page', 'claim_keys': sorted(item['claims']),
                         'editorial_note': item['note']}, item['source_checked_at'],
                        'curated_official_page', 'source_text_not_redistributed')
        source_id = put_source(db, source)
        for key, value in item['claims'].items():
            put_assertion(db, registry, item['id'], key, value, source_id, 'official_policy', 0.85,
                          observed_at=item['source_checked_at'])
    hotel = json.loads((ROOT / 'data' / 'pilot_hotel.json').read_text(encoding='utf-8'))
    coord = Source('partner_listing', hotel['id'] + ':coordinates',
                   'https://zhemchuzhina.broni.travel/contacts/',
                   {'hotel_id': hotel['id'], 'lat': hotel['lat'], 'lon': hotel['lon'],
                    'kind': 'coordinates', 'status': 'third_party_reported'},
                   '2026-10-26T18:00:00+00:00', 'curated_third_party', 'not_assessed')
    put_source(db, coord)

def seed_demo_hotels(db):
    from .connectors import Source
    registry = Registry()
    for item in json.loads((ROOT / 'data' / 'demo_hotels.json').read_text(encoding='utf-8')):
        hotel = {key: value for key, value in item.items() if key != 'facts'}
        db.execute('INSERT OR IGNORE INTO hotels(id,name,address,lat,lon,coord_status,offer_id) VALUES (:id,:name,:address,:lat,:lon,:coord_status,:offer_id)', hotel)
        source = Source('synthetic_demo', item['id'], 'urn:ostrovok:demo:' + item['id'],
                        {'notice': 'Illustrative fictional hotel and fictional characteristics'},
                        '2026-10-26T00:00:00Z', 'synthetic_fixture', 'synthetic')
        sid = put_source(db, source)
        for key, value in item['facts'].items():
            put_assertion(db, registry, hotel['id'], key, value, sid, 'synthetic_demo', 0.2)


def one(db, hotel_id):
    record = db.execute('SELECT * FROM hotels WHERE id=?', (hotel_id,)).fetchone()
    return dict(record) if record else None


def list_hotels(db):
    return [dict(row) for row in db.execute('SELECT * FROM hotels ORDER BY name')]


def put_source(db, source):
    record = source.as_dict()
    sha = source.digest
    source_id = hashlib.sha256(f'{source.provider}:{source.external_id}:{sha}'.encode()).hexdigest()[:32]
    db.execute('INSERT OR IGNORE INTO source_records VALUES (?,?,?,?,?,?,?,?,?)',
               (source_id, source.provider, source.external_id, source.url, encode(source.payload), sha,
                source.retrieved_at, source.provenance, source.license))
    if source.provenance == 'live':
        db.execute('UPDATE source_records SET retrieved_at=? WHERE id=? AND retrieved_at<?',
                   (source.retrieved_at, source_id, source.retrieved_at))
    return source_id


def put_assertion(db, registry, hotel_id, feature_key, value, source_id, method, confidence,
                  context=None, observed_at=None, algorithm_version=None, input_source_ids=None):
    feature = registry.get(feature_key)
    registry.validate(feature_key, value)
    if not (0 <= confidence <= 1):
        raise ValueError('Invalid confidence')
    if not one(db, hotel_id):
        raise ValueError('Unknown hotel')
    context = context or {}
    vjson, cjson = encode(value), encode(context)
    digest = hashlib.sha256(encode([hotel_id, feature_key, vjson, source_id, feature['scope'], cjson, algorithm_version]).encode()).hexdigest()[:32]
    db.execute('''INSERT OR IGNORE INTO assertions
    (id,hotel_id,feature_key,value_json,unit,source_id,method,confidence,observed_at,scope,context_json,algorithm_version,input_source_ids)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
    (digest, hotel_id, feature_key, vjson, feature.get('unit'), source_id, method, confidence,
     observed_at or iso_now(), feature['scope'], cjson, algorithm_version, encode(input_source_ids or [source_id])))
    if method == 'derived':
        record = db.execute('SELECT provenance FROM source_records WHERE id=?', (source_id,)).fetchone()
        if record and record['provenance'] == 'live':
            db.execute('UPDATE assertions SET observed_at=? WHERE id=? AND observed_at<?',
                       (observed_at or iso_now(), digest, observed_at or iso_now()))
    resolve(db, hotel_id, feature_key, context)
    return digest


def resolve(db, hotel_id, feature_key, context=None):
    context_json = encode(context or {})
    data = db.execute('''SELECT a.*, s.provider,s.provenance FROM assertions a
        JOIN source_records s ON a.source_id=s.id
        WHERE a.hotel_id=? AND a.feature_key=? AND a.context_json=?
        ORDER BY a.confidence DESC''', (hotel_id, feature_key, context_json)).fetchall()
    real_data = [row for row in data if row['provenance'] != 'synthetic_fixture']
    if real_data:
        data = real_data
    derived = {}
    selected = []
    for row in data:
        if row['method'] == 'derived':
            key = (row['provider'], row['method'])
            if key not in derived or (row['observed_at'], row['id']) > (derived[key]['observed_at'], derived[key]['id']):
                derived[key] = row
        else:
            selected.append(row)
    data = sorted(selected + list(derived.values()), key=lambda row: -row['confidence'])
    previous = db.execute('SELECT value_json,status FROM resolved_facts WHERE hotel_id=? AND feature_key=? AND context_json=?',
                          (hotel_id, feature_key, context_json)).fetchone()
    if not data:
        value, status, reason = None, 'unknown', 'No evidence collected'
    elif len({row['value_json'] for row in data}) > 1:
        value, status, reason = None, 'conflicted', 'Incompatible evidence; review required'
    else:
        best = data[0]
        value = best['value_json']
        if any(row['provenance'] == 'synthetic_fixture' for row in data):
            status, reason = 'estimated', 'Synthetic geometry for pipeline demonstration'
        elif best['method'] == 'derived':
            status, reason = 'estimated', 'Computed from source geometry'
        elif best['provider'] == 'hotelier':
            status, reason = 'reported', 'Hotelier-confirmed answer'
        elif best['provenance'] == 'live' and best['provider'] == 'wikidata':
            status, reason = 'reported', 'Wikidata record; entity identity requires verification'
        else:
            status, reason = 'reported', 'Source reported value, not independently verified'
    ids = [row['id'] for row in data]
    db.execute('''INSERT INTO resolved_facts
       (hotel_id,feature_key,context_json,value_json,status,assertion_ids,rationale,updated_at)
       VALUES (?,?,?,?,?,?,?,?)
       ON CONFLICT(hotel_id,feature_key,context_json) DO UPDATE SET
       value_json=excluded.value_json,status=excluded.status,assertion_ids=excluded.assertion_ids,
       rationale=excluded.rationale,updated_at=excluded.updated_at''',
       (hotel_id, feature_key, context_json, value, status, encode(ids), reason, iso_now()))
    if previous and (previous['value_json'] != value or previous['status'] != status):
        audit(db, 'fact.changed', hotel_id, {'feature_key': feature_key,
             'old_value': json.loads(previous['value_json']) if previous['value_json'] is not None else None,
             'new_value': json.loads(value) if value is not None else None,
             'old_status': previous['status'], 'new_status': status})
    if status == 'conflicted' and (not previous or previous['status'] != 'conflicted'):
        audit(db, 'fact.conflict_detected', hotel_id, {'feature_key': feature_key, 'assertion_ids': ids})
    return {'value': json.loads(value) if value is not None else None, 'status': status,
            'reason': reason, 'assertion_ids': ids}


def get_profile(db, hotel_id, registry):
    hotel = one(db, hotel_id)
    if not hotel:
        return None
    facts = {}
    for key in registry.items:
        record = db.execute('SELECT * FROM resolved_facts WHERE hotel_id=? AND feature_key=? AND context_json=?',
                            (hotel_id, key, '{}')).fetchone()
        assertion_ids = json.loads(record['assertion_ids']) if record else []
        latest = (db.execute('SELECT confidence,observed_at FROM assertions WHERE id=?', (assertion_ids[0],)).fetchone()
                  if assertion_ids else None)
        expiry = None
        stale = False
        if latest and registry.get(key).get('ttl_days'):
            try:
                observed = datetime.fromisoformat(latest['observed_at'].replace('Z', '+00:00'))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                expiry = observed + timedelta(days=registry.get(key)['ttl_days'])
                stale = expiry < datetime.now(timezone.utc)
            except (ValueError, TypeError):
                stale = True
        facts[key] = {'value': json.loads(record['value_json']) if record and record['value_json'] is not None else None,
                      'status': ('stale' if stale and record and record['status'] not in ('unknown', 'conflicted') else record['status']) if record else 'unknown',
                      'reason': record['rationale'] if record else 'No evidence collected',
                      'assertion_ids': assertion_ids,
                      'confidence': latest['confidence'] if latest else None,
                      'observed_at': latest['observed_at'] if latest else None,
                      'expires_at': expiry.isoformat() if expiry else None}
    return {'hotel': hotel, 'facts': facts}


def get_evidence(db, hotel_id, feature_key):
    records = db.execute('''SELECT a.*,s.provider,s.external_id,s.url,s.retrieved_at,s.provenance,s.license,s.payload
      FROM assertions a JOIN source_records s ON a.source_id=s.id
      WHERE a.hotel_id=? AND a.feature_key=? ORDER BY a.observed_at DESC''', (hotel_id, feature_key)).fetchall()
    out = []
    for row in records:
        item = dict(row)
        source_payload = json.loads(item.pop('payload'))
        item['source_note'] = source_payload.get('editorial_note')
        for name in ['value_json', 'context_json', 'input_source_ids']:
            item[name[:-5] if name.endswith('_json') else name] = json.loads(item.pop(name))
        out.append(item)
    return out


def audit(db, action, hotel_id, detail):
    db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?)',
               (str(uuid.uuid4()), action, hotel_id, encode(detail), iso_now()))


def stats(db, registry):
    profiles = [get_profile(db, hotel['id'], registry) for hotel in list_hotels(db)]
    total = len(profiles) * len(registry.items)
    published = sum(fact['status'] in ('verified', 'reported', 'estimated')
                    for profile in profiles for fact in profile['facts'].values())
    conflicts = sum(fact['status'] == 'conflicted'
                    for profile in profiles for fact in profile['facts'].values())
    verified = sum(fact['status'] == 'verified'
                   for profile in profiles for fact in profile['facts'].values())
    return {'hotels': len(profiles), 'features': len(registry.items), 'evidenced_slots': published,
            'unknown_slots': total - published, 'coverage': round(published / max(1, total), 3),
            'verified_share': round(verified / max(1, published), 3),
            'conflicts': conflicts, 'labeled_accuracy': None,
            'accuracy_reason': 'No independent labeled evaluation yet'}
