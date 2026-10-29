import hashlib
import json
import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx

from .connectors import Source, now_utc
from .geometry import haversine_m

OVERPASS_DEFAULT = 'https://overpass-api.de/api/interpreter'
VALHALLA_DEFAULT = 'https://valhalla1.openstreetmap.de/route'
HEADERS = {'User-Agent': 'Ostrovok-Intelligence-Prototype/0.7 (research; see repository README)',
           'X-Client-Id': 'ostrovok-intelligence-prototype'}


def validate_point(lat, lon):
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        raise ValueError('Coordinates must be numeric')
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Invalid coordinates')
    return float(lat), float(lon)


def osm_point(element):
    position = element.get('center', element)
    if 'lat' not in position or 'lon' not in position:
        return None
    return validate_point(position['lat'], position['lon'])


def normalized(value):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w ]+', ' ', str(value).lower().replace('ё', 'е'))).strip()


def hotel_match_score(hotel_name, hotel_address, tags):
    names = [tags.get(k) for k in ('name', 'name:ru', 'name:en', 'brand', 'official_name') if tags.get(k)]
    name = normalized(hotel_name)
    similarity = max((SequenceMatcher(None, name, normalized(other)).ratio() for other in names), default=0)
    if name and names:
        hparts = set(name.split()) - {'отель', 'гранд', 'hotel', 'sochi', 'сочи', 'centre', 'центр'}
        for other in names:
            oparts = set(normalized(other).split())
            if hparts and hparts.issubset(oparts):
                similarity = max(similarity, .90)
    house = re.search(r'(?:д\.?\s*)?(\d+\s*[а-яa-z]?(?:/\d+)?)\s*$', normalized(hotel_address))
    osm_house = normalized(tags.get('addr:housenumber', '')).replace(' ', '')
    house_match = bool(house and osm_house and house.group(1).replace(' ', '') == osm_house)
    address_bonus = .1 if house_match else 0
    return round(min(1., .9 * similarity + address_bonus), 3)


class OverpassPlaces:
    def __init__(self, endpoint=OVERPASS_DEFAULT, client=None):
        self.endpoint = endpoint
        self.client = client

    def _execute(self, query):
        client = self.client or httpx.Client(timeout=28, headers=HEADERS)
        try:
            response = client.post(self.endpoint, data={'data': query})
            response.raise_for_status()
            data = response.json()
            if data.get('remark'):
                raise ValueError('Overpass returned a partial or failed query')
            if not isinstance(data.get('elements'), list):
                raise ValueError('Overpass response missing elements')
            return data['elements']
        finally:
            if self.client is None:
                client.close()

    def find_hotels(self, hotel_name, hotel_address, region_lat=43.53, region_lon=39.82):
        validate_point(region_lat, region_lon)
        query = f'[out:json][timeout:25];nwr(around:35000,{region_lat:.5f},{region_lon:.5f})["tourism"="hotel"];out center tags 1300;'
        results = []
        for item in self._execute(query):
            try:
                point = osm_point(item)
                if point is None:
                    continue
                tags = item.get('tags') or {}
                score = hotel_match_score(hotel_name, hotel_address, tags)
                if score < .40:
                    continue
                if not (43.3 < point[0] < 44.0 and 39.3 < point[1] < 40.3):
                    continue
                key = f'{item["type"]}/{item["id"]}'
                results.append({'osm_id': key, 'lat': point[0], 'lon': point[1], 'score': score,
                                'name': tags.get('name', ''), 'tags': tags,
                                'url': f'https://www.openstreetmap.org/{key}'})
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(results, key=lambda item: -item['score'])[:8]

    def find_beaches(self, lat, lon, radius_m=4000):
        validate_point(lat, lon)
        if not (100 <= radius_m <= 8000):
            raise ValueError('Invalid beach search radius')
        query = (f'[out:json][timeout:25];(nwr(around:{radius_m},{lat:.6f},{lon:.6f})["natural"="beach"];'
                 f'nwr(around:{radius_m},{lat:.6f},{lon:.6f})["leisure"="beach"];);out center tags 200;')
        beaches = {}
        for item in self._execute(query):
            try:
                point = osm_point(item)
                if point is None:
                    continue
                tags = item.get('tags') or {}
                if tags.get('access') in ('private', 'no') or tags.get('foot') in ('no', 'private'):
                    continue
                osm_id = f'{item["type"]}/{item["id"]}'
                beaches[osm_id] = {'osm_id': osm_id, 'lat': point[0], 'lon': point[1],
                                   'name': tags.get('name') or 'Пляж (OSM)',
                                   'access': tags.get('access', 'unknown'),
                                   'point_kind': 'mapped_node' if item['type'] == 'node' else 'geometry_center',
                                   'straight_m': round(haversine_m(lat, lon, *point)),
                                   'url': f'https://www.openstreetmap.org/{osm_id}'}
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(beaches.values(), key=lambda item: item['straight_m'])[:15]


class ValhallaPedestrian:
    def __init__(self, endpoint=VALHALLA_DEFAULT, client=None):
        self.endpoint = endpoint
        self.client = client

    def route(self, origin, destination):
        lat1, lon1 = validate_point(*origin)
        lat2, lon2 = validate_point(*destination)
        body = {'locations': [{'lat': lat1, 'lon': lon1}, {'lat': lat2, 'lon': lon2}],
                'costing': 'pedestrian', 'units': 'km', 'directions_type': 'none'}
        client = self.client or httpx.Client(timeout=25, headers=HEADERS)
        try:
            response = client.post(self.endpoint, json=body)
            response.raise_for_status()
            data = response.json()
        finally:
            if self.client is None:
                client.close()
        try:
            summary = data['trip']['summary']
            length_m = round(float(summary['length']) * 1000)
            seconds = float(summary['time'])
            if not (0 < length_m <= 100_000 and 0 < seconds <= 86_400):
                raise ValueError('Implausible pedestrian route')
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError('Invalid pedestrian route response') from error
        return {'distance_m': length_m, 'duration_seconds': round(seconds), 'duration_minutes': round(seconds / 60, 1),
                'origin': [lat1, lon1], 'destination': [lat2, lon2], 'costing': 'pedestrian'}


def route_source(hotel_id, route, beach):
    return Source('valhalla_pedestrian', f'{hotel_id}:nearest-mapped-beach',
                  beach['url'], {'hotel_id': hotel_id, 'route': route, 'beach': beach,
                                 'access_verified': False, 'note': 'OSM beach point, not verified public entrance'},
                  now_utc(), 'live', 'ODbL_derived')


def beach_source(hotel_id, beaches):
    return Source('osm_beaches', hotel_id, 'https://www.openstreetmap.org/copyright',
                  {'hotel_id': hotel_id, 'beaches': beaches}, now_utc(), 'live', 'ODbL')


def candidate_source(hotel_id, candidate):
    return Source('osm_hotel_candidate', f'{hotel_id}:{candidate["osm_id"]}', candidate['url'],
                  {'hotel_id': hotel_id, **candidate}, now_utc(), 'live', 'ODbL')


def candidate_id(hotel_id, osm_id, lat, lon):
    return hashlib.sha256(f'{hotel_id}:{osm_id}:{lat:.7f}:{lon:.7f}'.encode()).hexdigest()[:32]
