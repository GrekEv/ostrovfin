import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    import psycopg
    dsn = os.getenv('DATABASE_URL')
    if not dsn:
        raise RuntimeError('DATABASE_URL is required')
    sql = (ROOT / 'sql/001_postgres_runtime.sql').read_text(encoding='utf-8')
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql)
    print('Intelligence schema ready')


if __name__ == '__main__':
    main()
