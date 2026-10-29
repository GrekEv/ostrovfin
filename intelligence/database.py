import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE = ROOT / 'data' / 'intelligence-v9.sqlite3'
POSTGRES_MIGRATION = ROOT / 'sql' / '001_intelligence_postgis.sql'


class Record(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return Record(row) if row is not None else None

    def fetchall(self):
        return [Record(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        for row in self.cursor:
            yield Record(row)


def postgres_sql(query):
    original = query.lstrip()
    if re.match(r'INSERT\s+OR\s+IGNORE\s+INTO\b', original, re.I):
        query = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO\b', 'INSERT INTO', query, count=1, flags=re.I)
        query = query.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    query = re.sub(r'(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)', r'%(\1)s', query)
    return query.replace('?', '%s')


class PostgresSession:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=None):
        return Result(self.connection.execute(postgres_sql(query), params))

    def executescript(self, sql):
        self.connection.execute(sql)


@contextmanager
def session(path=None):
    storage = os.getenv('OSTROVOK_STORAGE', 'sqlite').lower()
    if path is None and storage == 'postgres':
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise RuntimeError('Install psycopg[binary] to use PostgreSQL') from error
        url = os.getenv('OSTROVOK_DATABASE_URL', '')
        if not url:
            raise RuntimeError('OSTROVOK_DATABASE_URL is required for PostgreSQL')
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as connection:
            connection.execute('SET search_path TO intelligence, public')
            yield PostgresSession(connection)
    elif storage in ('sqlite', 'postgres'):
        db_path = Path(path or os.getenv('OSTROVOK_INTELLIGENCE_DB', str(DEFAULT_SQLITE))).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    else:
        raise ValueError('OSTROVOK_STORAGE must be sqlite or postgres')


def check_schema(db):
    row = db.execute("""SELECT data_type FROM information_schema.columns
        WHERE table_schema='intelligence' AND table_name='hotels' AND column_name='id'""").fetchone()
    if not row or row['data_type'] != 'text':
        raise RuntimeError('Incompatible intelligence schema: expected hotels.id TEXT; use a new database or a reviewed migration')
    for table, column in [('hotels', 'lat'), ('hotels', 'location'),
                          ('assertions', 'value_json'), ('source_records', 'payload'),
                          ('geo_features', 'geom')]:
        exists = db.execute("""SELECT 1 FROM information_schema.columns
            WHERE table_schema='intelligence' AND table_name=? AND column_name=?""", (table, column)).fetchone()
        if not exists:
            raise RuntimeError(f'Incompatible intelligence schema: missing {table}.{column}')


def migrate():
    if os.getenv('OSTROVOK_STORAGE', 'sqlite').lower() != 'postgres':
        raise RuntimeError('PostGIS migration needs OSTROVOK_STORAGE=postgres')
    with session() as db:
        db.executescript(POSTGRES_MIGRATION.read_text(encoding='utf-8'))
        nullable_migration = ROOT / 'sql' / '002_nullable_coordinates.sql'
        db.executescript(nullable_migration.read_text(encoding='utf-8'))
        db.executescript((ROOT / 'sql' / '003_geo_candidates.sql').read_text(encoding='utf-8'))
        db.executescript((ROOT / 'sql' / '004_research_v8.sql').read_text(encoding='utf-8'))
        db.executescript((ROOT / 'sql' / '005_operator_v9.sql').read_text(encoding='utf-8'))
        check_schema(db)


if __name__ == '__main__':
    migrate()
    print('Intelligence schema migrated')
