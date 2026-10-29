import hashlib
import json
from urllib.parse import quote, urlparse

import httpx

from .connectors import Source, now_utc
from .store import audit, encode, one, put_source
from .urban import distance_m


def commons_url(url):
    parsed = urlparse(url)
    return parsed.scheme == 'https' and (parsed.hostname == 'commons.wikimedia.org' or
                                          (parsed.hostname or '').endswith('.wikimedia.org'))


class CommonsGeosearch:
    def __init__(self, endpoint='https://commons.wikimedia.org/w/api.php', client=None):
        if not commons_url(endpoint):
            raise ValueError('Commons endpoint must use Wikimedia HTTPS')
        self.endpoint = endpoint
        self.client = client

    def discover(self, lat, lon, radius_m=500):
        if not 50 <= radius_m <= 1000:
            raise ValueError('Radius must be between 50 and 1000 m')
        params = {'action': 'query', 'format': 'json', 'generator': 'geosearch',
                  'ggscoord': f'{lat:.6f}|{lon:.6f}', 'ggsradius': radius_m,
                  'ggsnamespace': 6, 'ggslimit': 15,
                  'prop': 'coordinates|imageinfo', 'iiprop': 'url|mime|extmetadata',
                  'iiurlwidth': 600}
        client = self.client or httpx.Client(timeout=25, headers={'User-Agent': 'OstrovokResearchPilot/1.0 (evidence discovery)'})
        try:
            response = client.get(self.endpoint, params=params)
            response.raise_for_status()
            pages = response.json().get('query', {}).get('pages', {})
        finally:
            if self.client is None:
                client.close()
        result = []
        for page in pages.values():
            points = page.get('coordinates') or []
            images = page.get('imageinfo') or []
            if not points or not images or page.get('ns') != 6:
                continue
            photo = images[0]
            if not str(photo.get('mime', '')).startswith('image/'):
                continue
            coord = points[0]
            if not all(isinstance(coord.get(x), (float, int)) for x in ('lat', 'lon')):
                continue
            dist = distance_m(lat, lon, coord['lat'], coord['lon'])
            if dist > radius_m:
                continue
            title = str(page.get('title', ''))
            if not title.startswith('File:'):
                continue
            file_page = 'https://commons.wikimedia.org/wiki/' + quote(title.replace(' ', '_'), safe=':()')
            thumbnail = photo.get('thumburl', '')
            metadata = photo.get('extmetadata', {})
            license_label = metadata.get('LicenseShortName', {}).get('value', 'unknown')
            result.append({'title': title[:400], 'page_url': file_page,
                           'thumbnail_url': thumbnail if commons_url(thumbnail) else None,
                           'lat': coord['lat'], 'lon': coord['lon'], 'distance_m': dist,
                           'license': str(license_label)[:120]})
        return sorted(result, key=lambda p: p['distance_m'])[:15]


def save_candidates(db, hotel_id, results):
    hotel = one(db, hotel_id)
    if hotel is None:
        raise ValueError('Unknown hotel')
    if hotel['lat'] is None or hotel['lon'] is None or hotel['coord_status'] == 'synthetic_fixture':
        raise ValueError('Sourced hotel coordinates required')
    saved = []
    for item in results:
        source = Source('wikimedia_commons', item['title'], item['page_url'], item,
                        now_utc(), 'live', item['license'])
        sid = put_source(db, source)
        identifier = hashlib.sha256(encode([hotel_id, item['title'], item['lat'], item['lon']]).encode()).hexdigest()[:32]
        db.execute('''INSERT OR IGNORE INTO media_candidates
            (id,hotel_id,title,lat,lon,distance_m,source_id,status,created_at)
            VALUES (?,?,?,?,?,?,?,'pending',?)''',
            (identifier, hotel_id, item['title'], item['lat'], item['lon'], item['distance_m'], sid, now_utc()))
        saved.append(identifier)
    if saved:
        audit(db, 'media.discovered', hotel_id, {'count': len(saved), 'ids': saved})
    return saved


def list_media(db, hotel_id, public=False):
    if not one(db, hotel_id):
        raise ValueError('Unknown hotel')
    clause = "AND c.status='reviewed'" if public else ''
    rows = db.execute('''SELECT c.*,s.url,s.payload,s.license FROM media_candidates c
        JOIN source_records s ON c.source_id=s.id WHERE c.hotel_id=? ''' + clause +
        ' ORDER BY c.distance_m LIMIT 50', (hotel_id,)).fetchall()
    return [{'id': x['id'], 'title': x['title'], 'distance_m': x['distance_m'],
             'page_url': x['url'], 'license': x['license'], 'status': x['status'],
             'thumbnail_url': json.loads(x['payload']).get('thumbnail_url')} for x in rows]


def review_media(db, hotel_id, candidate_id, approved):
    row = db.execute('SELECT id FROM media_candidates WHERE id=? AND hotel_id=?',
                     (candidate_id, hotel_id)).fetchone()
    if row is None:
        raise ValueError('Unknown media candidate')
    status = 'reviewed' if approved else 'rejected'
    db.execute('UPDATE media_candidates SET status=? WHERE id=?', (status, candidate_id))
    audit(db, 'media.reviewed', hotel_id, {'id': candidate_id, 'status': status})
    return {'id': candidate_id, 'status': status, 'view_confirmed': False}
