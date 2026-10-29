import hashlib
import json
from .database import PostgresSession


def geojson_multiline(lines):
    if not lines or any(len(line) < 2 for line in lines):
        raise ValueError('Expected nonempty line geometries')
    for line in lines:
        for point in line:
            if len(point) != 2 or not (-90 <= point[0] <= 90 and -180 <= point[1] <= 180):
                raise ValueError('Invalid source coordinate')
    return json.dumps({'type': 'MultiLineString', 'coordinates': [
        [[point[1], point[0]] for point in line] for line in lines
    ]}, separators=(',', ':'))


def distance_to_lines(db, hotel_id, lines, source_id, store_geometry=True):
    if not isinstance(db, PostgresSession):
        raise TypeError('PostGIS session required')
    payload = geojson_multiline(lines)
    row = db.execute('''SELECT ROUND(ST_Distance(h.location,
        ST_GeomFromGeoJSON(?)::geography)::numeric)::integer AS distance_m
        FROM hotels h WHERE h.id=?''', (payload, hotel_id)).fetchone()
    if row is None or row['distance_m'] is None:
        raise ValueError('Unknown hotel or invalid coastline geometry')
    if store_geometry:
        geometry_id = hashlib.sha256((source_id + ':coastline').encode()).hexdigest()[:32]
        db.execute('''INSERT INTO geo_features(id,source_id,category,geom)
        VALUES(?,?,'coastline',ST_GeomFromGeoJSON(?)::geography)
        ON CONFLICT (id) DO NOTHING''', (geometry_id, source_id, payload))
    return int(row['distance_m'])
