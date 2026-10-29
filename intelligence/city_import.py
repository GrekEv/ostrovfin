import argparse
import csv
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from .database import session
from .urban import CATEGORIES, submit_record


class CityNotice(BaseModel):
    provider: str = Field(min_length=3, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    source_reference: str = Field(min_length=3, max_length=180)
    source_url: str = Field(max_length=2000)
    category: str
    title: str = Field(min_length=5, max_length=180)
    description: str = Field(min_length=5, max_length=1000)
    starts_on: date
    ends_on: date
    lat: float = Field(ge=43.30, le=43.80)
    lon: float = Field(ge=39.40, le=40.20)
    radius_m: float = Field(default=0, ge=0, le=2000)

    @model_validator(mode='after')
    def verify(self):
        if self.category not in CATEGORIES:
            raise ValueError('Unsupported notice category')
        if self.ends_on < self.starts_on:
            raise ValueError('Invalid notice period')
        url = urlparse(self.source_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('A public HTTPS source URL is required')
        return self

    def store_data(self):
        return {**self.model_dump(mode='json')}


def parse_notices(text, fmt):
    if fmt == 'csv':
        rows = list(csv.DictReader(text.splitlines()))
    elif fmt == 'json':
        rows = json.loads(text)
        if isinstance(rows, dict):
            rows = rows.get('records')
    else:
        raise ValueError('Expected JSON or CSV source file')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 250:
        raise ValueError('Import must contain 1..250 records')
    notices = [CityNotice.model_validate(row).store_data() for row in rows]
    keys = [(x['provider'], x['source_reference']) for x in notices]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate provider/reference in one batch')
    return notices


def load_notices(filename):
    path = Path(filename)
    if path.stat().st_size > 2_000_000:
        raise ValueError('Source file exceeds 2 MB')
    return parse_notices(path.read_text(encoding='utf-8-sig'), path.suffix.lower().lstrip('.'))


def import_notices(db, notices, apply=False):
    items = []
    for record in notices:
        existing = db.execute('''SELECT u.id,u.status,s.payload,s.url FROM urban_records u
          JOIN source_records s ON s.id=u.source_id
          WHERE s.provider=? AND s.external_id=? ORDER BY u.created_at DESC,u.id DESC''',
          (record['provider'], record['source_reference'])).fetchall()
        existing_values = [(json.loads(x['payload']), x['url']) for x in existing]
        payload = {'category': record['category'], 'title': record['title'],
                   'starts_on': record['starts_on'], 'ends_on': record['ends_on'],
                   'lat': record['lat'], 'lon': record['lon'], 'radius_m': record['radius_m'],
                   'note': record['description']}
        action = 'unchanged' if (payload, record['source_url']) in existing_values else ('revision' if existing else 'new')
        item = {'provider': record['provider'], 'reference': record['source_reference'],
                'action': action, 'review_required': action != 'unchanged'}
        if apply and action != 'unchanged':
            item['id'] = submit_record(db, record)
        items.append(item)
    return {'applied': apply, 'total': len(items),
            'new': sum(x['action']=='new' for x in items),
            'revised': sum(x['action']=='revision' for x in items),
            'unchanged': sum(x['action']=='unchanged' for x in items),
            'items': items,
            'notice': 'Imported items remain pending until an operator approves them'}


def main():
    parser = argparse.ArgumentParser(description='Validate and stage city notices')
    parser.add_argument('file', help='CSV or JSON file of Sochi municipal notices')
    parser.add_argument('--apply', action='store_true', help='Persist pending records')
    args = parser.parse_args()
    notices = load_notices(args.file)
    from .store import initialize
    initialize()
    with session() as db:
        result = import_notices(db, notices, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
