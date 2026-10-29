import json
import os
import uuid
from pathlib import Path
import httpx
from .connectors import OverpassConnector, SnapshotConnector, Source, WikidataConnector, now_utc
from .geometry import line_distance_m
from .geoagent import OverpassPlaces, ValhallaPedestrian, beach_source, route_source
from .database import PostgresSession
from .geopostgis import distance_to_lines
from .postgres import calculate_coast_distance
from .registry import Registry
from .store import audit, encode, iso_now, one, put_assertion, put_source, session

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_CALCULATORS = {'coast_straight_m': 'geo.shortest_distance_to_coast_v1',
                         'beach_route_m': 'geo.valhalla_pedestrian_to_osm_beach_v1',
                         'beach_walk_minutes': 'geo.valhalla_pedestrian_to_osm_beach_v1'}


def plan(feature_keys, registry):
    steps = []
    for key in feature_keys:
        feature = registry.get(key)
        if key in SUPPORTED_CALCULATORS:
            steps.append({'feature_key': key, 'tool': 'osm_overpass' if key == 'coast_straight_m' else 'osm_beaches_valhalla',
                          'fallback': 'synthetic_fixture_demo_only' if key == 'coast_straight_m' else None})
        elif key == 'building_year':
            steps.append({'feature_key': key, 'tool': 'wikidata', 'requires': 'verified_qid'})
        else:
            steps.append({'feature_key': key, 'tool': 'hotelier', 'requires': feature['scope']})
    return steps


def create_job(db, hotel_id, keys, mode, key, registry):
    if not one(db, hotel_id):
        raise ValueError('Unknown hotel')
    for feature_key in keys:
        registry.get(feature_key)
    if not key or len(key) > 128:
        raise ValueError('Idempotency key required')
    existing = db.execute('SELECT * FROM jobs WHERE idempotency_key=?', (key,)).fetchone()
    if existing:
        if existing['hotel_id'] != hotel_id or json.loads(existing['feature_keys']) != keys or existing['mode'] != mode:
            raise ValueError('Idempotency key payload mismatch')
        return existing['id'], False
    job_id = str(uuid.uuid4())
    now = iso_now()
    db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?, ?,?)',
               (job_id, hotel_id, encode(keys), mode, key, 'queued', '[]', now, now))
    return job_id, True


def run_job(job_id, path=None, overpass=None, wikidata=None, places=None, router=None):
    registry = Registry()
    with session(path) as db:
        job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise ValueError('Unknown job')
        if job['status'] in ('completed', 'partial'):
            return json.loads(job['details_json'])
        hotel = one(db, job['hotel_id'])
        steps = plan(json.loads(job['feature_keys']), registry)
        db.execute('UPDATE jobs SET status=?,updated_at=? WHERE id=?', ('running', iso_now(), job_id))
        snapshots = SnapshotConnector().fetch(hotel['id'])
        stored = [put_source(db, item) for item in snapshots]
        coord_ids = [sid for item, sid in zip(snapshots, stored) if 'lat' in item.payload]
        approved = db.execute("SELECT source_id FROM geo_candidates WHERE hotel_id=? AND status='approved'",
                              (hotel['id'],)).fetchone()
        if approved:
            coord_ids = [approved['source_id']]
        elif hotel['coord_status'] == 'sourced_reviewed':
            coord_ids = []
            records = db.execute("SELECT id,payload FROM source_records WHERE provider='coordinate_review' ORDER BY retrieved_at DESC").fetchall()
            for record in records:
                payload = json.loads(record['payload'])
                if (payload.get('hotel_id') == hotel['id'] and
                    payload.get('lat') == hotel['lat'] and payload.get('lon') == hotel['lon']):
                    coord_ids = [record['id']]
                    break
        details = []
        route_context = None
        for step in steps:
            feature = step['feature_key']
            try:
                if feature == 'coast_straight_m':
                    if hotel['lat'] is None or hotel['lon'] is None:
                        details.append({'tool': 'geocoding', 'feature_key': feature,
                                        'state': 'needs_coordinates', 'reason': 'No sourced hotel location'})
                        continue
                    if job['mode'] == 'live' and not coord_ids:
                        details.append({'tool': 'geocoding', 'feature_key': feature,
                                        'state': 'needs_coordinates', 'reason': 'Coordinates lack traceable source'})
                        continue
                    if job['mode'] == 'demo':
                        fixture = json.loads((ROOT / 'data' / 'synthetic_coastline.json').read_text(encoding='utf-8'))
                        source = Source('synthetic_coastline', 'fixture-v1', 'https://www.openstreetmap.org/copyright',
                                        {'lines': fixture['geometry'], 'description': fixture['description']},
                                        now_utc(), 'synthetic_fixture', 'synthetic')
                    else:
                        source = (overpass or OverpassConnector(os.getenv('OVERPASS_URL', 'https://overpass-api.de/api/interpreter'))).fetch_coastlines(hotel['lat'], hotel['lon'])
                    source_id = put_source(db, source)
                    if os.getenv('OSTROVOK_STORAGE', 'sqlite').lower() == 'postgres':
                        distance = calculate_coast_distance(db, hotel['lat'], hotel['lon'], source_id, source.payload['lines'])
                    else:
                        distance = (distance_to_lines(db, hotel['id'], source.payload['lines'], source_id)
                                if isinstance(db, PostgresSession) else
                                line_distance_m(hotel['lat'], hotel['lon'], source.payload['lines']))
                    assertion_id = put_assertion(db, registry, hotel['id'], feature, distance, source_id,
                                                 'derived', 0.25 if job['mode'] == 'demo' else 0.75,
                                                 algorithm_version=('geo.postgis_geodesic_v1' if os.getenv('OSTROVOK_STORAGE') == 'postgres' else SUPPORTED_CALCULATORS[feature]),
                                                 input_source_ids=coord_ids + [source_id])
                    details.append({'tool': step['tool'], 'feature_key': feature, 'state': 'completed',
                                    'assertion_id': assertion_id, 'distance_m': distance,
                                    'data_kind': source.provenance})
                elif feature in ('beach_route_m', 'beach_walk_minutes'):
                    if hotel['lat'] is None or hotel['lon'] is None:
                        details.append({'tool': 'geocoding', 'feature_key': feature,
                                        'state': 'needs_coordinates', 'reason': 'No sourced hotel location'})
                        continue
                    if job['mode'] == 'live' and not coord_ids:
                        details.append({'tool': 'geocoding', 'feature_key': feature,
                                        'state': 'needs_coordinates', 'reason': 'Coordinates lack traceable source'})
                        continue
                    if job['mode'] != 'live':
                        details.append({'tool': 'osm_beaches_valhalla', 'feature_key': feature,
                                        'state': 'needs_live_route', 'reason': 'Pedestrian routing is live only'})
                        continue
                    if route_context is None:
                        location = (hotel['lat'], hotel['lon'])
                        destinations = (places or OverpassPlaces(os.getenv('OVERPASS_URL', 'https://overpass-api.de/api/interpreter'))).find_beaches(*location)
                        if not destinations:
                            raise ValueError('No mapped beaches found within search radius')
                        beach_id = put_source(db, beach_source(hotel['id'], destinations))
                        successes = []
                        for destination in destinations[:3]:
                            try:
                                path_result = (router or ValhallaPedestrian(os.getenv('VALHALLA_URL',
                                           'https://valhalla1.openstreetmap.de/route'))).route(
                                           location, (destination['lat'], destination['lon']))
                                successes.append((path_result, destination))
                            except (httpx.HTTPError, KeyError, ValueError, TimeoutError):
                                continue
                        if not successes:
                            raise ValueError('Pedestrian routes unavailable for nearest OSM beach points')
                        chosen_route, chosen_beach = min(successes, key=lambda item: item[0]['duration_seconds'])
                        route_record = route_source(hotel['id'], chosen_route, chosen_beach)
                        route_context = (chosen_route, put_source(db, route_record), beach_id, chosen_beach)
                    walk, source_id, beach_id, chosen_beach = route_context
                    number = walk['distance_m'] if feature == 'beach_route_m' else walk['duration_minutes']
                    assertion_id = put_assertion(db, registry, hotel['id'], feature, number, source_id,
                                                 'derived', 0.72, algorithm_version=SUPPORTED_CALCULATORS[feature],
                                                 input_source_ids=coord_ids + [beach_id, source_id])
                    details.append({'tool': 'osm_beaches_valhalla', 'feature_key': feature,
                                    'state': 'completed', 'assertion_id': assertion_id, 'value': number,
                                    'beach': chosen_beach['name'], 'access_verified': False,
                                    'data_kind': 'live'})
                elif feature == 'building_year':
                    from .heritage import building_identity
                    identity = building_identity(db, hotel['id'])
                    mapping = {'qid': identity.get('qid'), 'verified': identity['status'] == 'approved'}
                    if job['mode'] != 'live' or not mapping.get('verified') or not mapping.get('qid'):
                        details.append({'tool': 'wikidata', 'feature_key': feature, 'state': 'needs_identity_review',
                                        'reason': 'No independently matched building QID'})
                    else:
                        source = (wikidata or WikidataConnector()).fetch_building_year(mapping['qid'])
                        source_id = put_source(db, source)
                        if len(source.payload['years']) != 1:
                            details.append({'tool': 'wikidata', 'feature_key': feature, 'state': 'needs_review',
                                            'reason': 'No unique inception year'})
                        else:
                            year = source.payload['years'][0]
                            assertion_id = put_assertion(db, registry, hotel['id'], feature, year, source_id,
                                                         'observed', 0.65, algorithm_version='wikidata.P571_v1',
                                                         input_source_ids=[identity['source_id'], source_id])
                            details.append({'tool': 'wikidata', 'feature_key': feature, 'state': 'completed',
                                            'assertion_id': assertion_id, 'year': year, 'data_kind': 'live'})
                else:
                    details.append({'tool': 'hotelier', 'feature_key': feature, 'state': 'needs_hotelier',
                                    'reason': 'No direct primary source'})
            except (OSError, ValueError, KeyError, TimeoutError, RuntimeError, httpx.HTTPError) as error:
                details.append({'tool': step['tool'], 'feature_key': feature, 'state': 'failed',
                                'error_code': type(error).__name__, 'message': str(error)[:200]})
        status = 'completed' if all(x['state'] == 'completed' for x in details) else 'partial'
        db.execute('UPDATE jobs SET status=?,details_json=?,updated_at=? WHERE id=?',
                   (status, encode(details), iso_now(), job_id))
        audit(db, 'enrichment.completed', hotel['id'], {'job_id': job_id, 'status': status})
        return details
