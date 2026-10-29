import os
import re
from contextlib import contextmanager


class HybridRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def _convert(self, item):
        if item is None:
            return None
        return HybridRow(item)

    def fetchone(self):
        return self._convert(self.cursor.fetchone())

    def fetchall(self):
        return [self._convert(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        for item in self.cursor:
            yield self._convert(item)


class PgSession:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        statement = query.replace('?', '%s')
        if re.match(r'\s*INSERT\s+OR\s+IGNORE\s+INTO\b', statement, re.I):
            statement = re.sub(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b', 'INSERT INTO', statement, count=1, flags=re.I)
            statement += ' ON CONFLICT DO NOTHING'
        cursor = self.connection.execute(statement, params)
        return Result(cursor)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


@contextmanager
def session(dsn=None):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError('PostgreSQL requires psycopg; install requirements.txt') from exc
    address = dsn or os.getenv('DATABASE_URL')
    if not address:
        raise RuntimeError('DATABASE_URL is required for PostgreSQL mode')
    connection = psycopg.connect(address, row_factory=dict_row, options='-c search_path=intelligence,public')
    try:
        yield PgSession(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def wkt_lines(lines):
    result = []
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            continue
        points = []
        for point in line:
            if len(point) != 2:
                raise ValueError('Invalid geometry point')
            lat, lon = float(point[0]), float(point[1])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError('Invalid geometry coordinates')
            points.append(f'{lon:.8f} {lat:.8f}')
        result.append('LINESTRING(' + ','.join(points) + ')')
    if not result:
        raise ValueError('Source has no line geometries')
    return result


def calculate_coast_distance(db, lat, lon, source_id, lines):
    from hashlib import sha256
    wkts = wkt_lines(lines)
    for wkt in wkts:
        checksum = sha256(wkt.encode()).hexdigest()
        db.execute('''INSERT INTO source_geometries(source_id,segment_sha256,geom)
            VALUES (%s,%s,ST_GeomFromText(%s,4326)) ON CONFLICT DO NOTHING''',
                   (source_id, checksum, wkt))
    result = db.execute('''SELECT MIN(ST_Distance(
           ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, geom::geography)) AS metres
           FROM source_geometries WHERE source_id=%s''', (lon, lat, source_id)).fetchone()
    if result is None or result['metres'] is None:
        raise ValueError('No coastline geometry persisted')
    return round(result['metres'])
