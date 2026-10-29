import re

from .connectors import Source
from .store import audit, iso_now, one, put_source

QID = re.compile(r'^Q[1-9][0-9]*$')


def submit_identity(db, hotel_id, qid, reference_url, note):
    if not one(db, hotel_id):
        raise ValueError('Unknown hotel')
    if not QID.fullmatch(qid):
        raise ValueError('Invalid Wikidata ID')
    if not reference_url.startswith('https://'):
        raise ValueError('HTTPS identity evidence required')
    timestamp = iso_now()
    evidence = Source('building_identity_review', f'{hotel_id}:{qid}', reference_url,
                      {'hotel_id': hotel_id, 'qid': qid, 'review_note': note},
                      timestamp, 'operator_submitted', 'not_assessed')
    sid = put_source(db, evidence)
    db.execute('''INSERT INTO building_identities (hotel_id,qid,source_id,status,reviewed_at)
        VALUES (?,?,?,'pending',NULL)
        ON CONFLICT(hotel_id) DO UPDATE SET qid=excluded.qid,source_id=excluded.source_id,
        status='pending',reviewed_at=NULL''', (hotel_id, qid, sid))
    audit(db, 'building.identity_submitted', hotel_id, {'qid': qid, 'source_id': sid})
    return {'hotel_id': hotel_id, 'qid': qid, 'status': 'pending'}


def review_identity(db, hotel_id, approved):
    found = db.execute('SELECT * FROM building_identities WHERE hotel_id=?', (hotel_id,)).fetchone()
    if not found:
        raise ValueError('No building identity to review')
    status = 'approved' if approved else 'rejected'
    db.execute('UPDATE building_identities SET status=?,reviewed_at=? WHERE hotel_id=?',
               (status, iso_now(), hotel_id))
    audit(db, 'building.identity_reviewed', hotel_id, {'qid': found['qid'], 'status': status})
    return {'hotel_id': hotel_id, 'qid': found['qid'], 'status': status}


def building_identity(db, hotel_id):
    if not one(db, hotel_id):
        raise ValueError('Unknown hotel')
    row = db.execute('''SELECT b.*,s.url FROM building_identities b
        JOIN source_records s ON s.id=b.source_id WHERE b.hotel_id=?''', (hotel_id,)).fetchone()
    return (dict(row) if row else {'hotel_id': hotel_id, 'qid': None, 'status': 'unknown',
                                   'note': 'A hotel brand is not sufficient to identify its physical building'})


class WikidataBuildingInspector:
    def __init__(self, endpoint='https://www.wikidata.org/w/api.php', client=None):
        if endpoint != 'https://www.wikidata.org/w/api.php':
            raise ValueError('Unsupported Wikidata endpoint')
        self.endpoint = endpoint
        self.client = client

    def inspect(self, qid):
        import httpx
        if not QID.fullmatch(qid):
            raise ValueError('Invalid Wikidata ID')
        client = self.client or httpx.Client(timeout=20, headers={'User-Agent': 'OstrovokResearchPilot/1.0 (identity research)'})
        try:
            response = client.get(self.endpoint, params={'action': 'wbgetentities',
                    'ids': qid, 'props': 'labels|descriptions|sitelinks|claims',
                    'languages': 'ru|en', 'format': 'json'})
            response.raise_for_status()
            entity = response.json().get('entities', {}).get(qid, {})
        finally:
            if self.client is None:
                client.close()
        if not entity or entity.get('missing') is not None:
            raise ValueError('No Wikidata entity')
        labels = {lang: value.get('value', '') for lang, value in entity.get('labels', {}).items()}
        descriptions = {lang: value.get('value', '') for lang, value in entity.get('descriptions', {}).items()}
        sitelinks = {k: value.get('title', '') for k, value in entity.get('sitelinks', {}).items()
                     if k in ('ruwiki', 'enwiki')}
        inception = []
        for statement in entity.get('claims', {}).get('P571', []):
            value = statement.get('mainsnak', {}).get('datavalue', {}).get('value', {})
            if isinstance(value, dict) and value.get('time'):
                inception.append(str(value['time'])[:32])
        return {'qid': qid, 'labels': labels, 'descriptions': descriptions,
                'wikipedia_sitelinks': sitelinks, 'inception_statements': sorted(set(inception)),
                'historical_use_verified': False}


def inspect_identity(db, hotel_id, inspector=None):
    identity = building_identity(db, hotel_id)
    qid = identity.get('qid')
    if not qid:
        raise ValueError('No linked Wikidata item')
    result = (inspector or WikidataBuildingInspector()).inspect(qid)
    source = Source('wikidata_item', qid, f'https://www.wikidata.org/wiki/{qid}',
                    result, iso_now(), 'live', 'CC0')
    sid = put_source(db, source)
    return {'identity_status': identity['status'], 'source_id': sid,
            'candidate': result, 'building_history_fact_created': False}
