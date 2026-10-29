import argparse
import os
import sqlite3
from pathlib import Path
from .postgres import session as pg_session

TABLES = (
    'hotels', 'source_records', 'assertions', 'resolved_facts',
    'questions', 'answers', 'candidates', 'jobs', 'audit_events',
)


def migrate(source, dsn=None):
    location = Path(source)
    if not location.is_file():
        raise FileNotFoundError(location)
    sqlite = sqlite3.connect(f'file:{location.resolve()}?mode=ro', uri=True)
    sqlite.row_factory = sqlite3.Row
    counts = {}
    try:
        with pg_session(dsn) as db:
            for table in TABLES:
                cursor = sqlite.execute(f'SELECT * FROM {table}')
                columns = [column[0] for column in cursor.description]
                rows = cursor.fetchall()
                if rows:
                    fields = ','.join(columns)
                    placeholders = ','.join('%s' for _ in columns)
                    statement = f'INSERT INTO {table}({fields}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
                    for row in rows:
                        db.execute(statement, tuple(row))
                counts[table] = len(rows)
            for table in TABLES:
                ids = [row[0] for row in sqlite.execute(f'SELECT id FROM {table}')] if table not in ('resolved_facts',) else []
                if ids:
                    placeholders = ','.join('%s' for _ in ids)
                    if db.execute(f'SELECT COUNT(*) FROM {table} WHERE id IN ({placeholders})', ids).fetchone()[0] != len(ids):
                        raise RuntimeError(f'Import verification failed for {table}')
    finally:
        sqlite.close()
    return counts


def main():
    parser = argparse.ArgumentParser(description='Import a local SQLite Evidence Store into PostgreSQL')
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    counts = migrate(args.source, os.getenv('DATABASE_URL'))
    for table, count in counts.items():
        print(f'{table}: {count}')


if __name__ == '__main__':
    main()
