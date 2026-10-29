import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def now_utc():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Source:
    provider: str
    external_id: str
    url: str
    payload: dict
    retrieved_at: str
    provenance: str
    license: str = 'not_assessed'

    def as_dict(self):
        return vars(self).copy()

    @property
    def digest(self):
        content = json.dumps(self.payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()


class SnapshotConnector:
    key = 'snapshot'

    def fetch(self, hotel_id):
        snapshots = json.loads((ROOT / 'data' / 'source_snapshots.json').read_text(encoding='utf-8'))
        return [Source(**item) for item in snapshots if item['payload'].get('hotel_id') == hotel_id]


class OverpassConnector:
    key = 'osm_overpass'

    def __init__(self, endpoint='https://overpass-api.de/api/interpreter', client=None):
        self.endpoint = endpoint
        self.client = client

    def fetch_coastlines(self, lat, lon, radius_m=2500):
        if not (0 < radius_m <= 10000):
            raise ValueError('Invalid radius')
        query = f'[out:json][timeout:20];way(around:{int(radius_m)},{lat:.6f},{lon:.6f})["natural"="coastline"];out geom;'
        client = self.client or httpx.Client(timeout=25, headers={'User-Agent': 'OstrovokIntelligencePilot/1.0 (data research)'})
        try:
            response = client.post(self.endpoint, data={'data': query})
            response.raise_for_status()
            data = response.json()
        finally:
            if self.client is None:
                client.close()
        elements = data.get('elements', [])
        lines = [[[point['lat'], point['lon']] for point in item['geometry']] for item in elements if item.get('type') == 'way' and len(item.get('geometry', [])) >= 2]
        if not lines:
            raise ValueError('No coastline ways returned by Overpass')
        ids = [str(item['id']) for item in elements if item.get('type') == 'way' and 'geometry' in item]
        source = Source('osm_overpass', ','.join(ids), 'https://www.openstreetmap.org/copyright', {'lines': lines, 'osm_way_ids': ids}, now_utc(), 'live', 'ODbL')
        return source


class WikidataConnector:
    key = 'wikidata'

    def __init__(self, endpoint='https://query.wikidata.org/sparql', client=None):
        self.endpoint = endpoint
        self.client = client

    def fetch_building_year(self, qid):
        if not qid.startswith('Q') or not qid[1:].isdigit():
            raise ValueError('Invalid Wikidata ID')
        query = f'SELECT ?inception WHERE {{ wd:{qid} wdt:P571 ?inception. }} LIMIT 2'
        client = self.client or httpx.Client(timeout=20, headers={'User-Agent': 'OstrovokIntelligencePilot/1.0 (data research)'})
        try:
            response = client.get(self.endpoint, params={'query': query, 'format': 'json'}, headers={'Accept':'application/sparql-results+json'})
            response.raise_for_status()
            bindings = response.json()['results']['bindings']
        finally:
            if self.client is None:
                client.close()
        years = sorted({int(row['inception']['value'][0:4]) for row in bindings if 'inception' in row})
        return Source('wikidata', qid, f'https://www.wikidata.org/wiki/{qid}', {'qid': qid, 'years': years}, now_utc(), 'live', 'CC0')
