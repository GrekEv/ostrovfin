import argparse
import ipaddress
import json
import os
from urllib.parse import urlparse

import httpx

from .city_import import import_notices, parse_notices
from .database import session
from .store import initialize

MAX_BYTES = 2_000_000


class CityFeedClient:
    def __init__(self, url, allowlist, client=None):
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        hosts = {item.strip().lower() for item in allowlist.split(',') if item.strip()}
        if parsed.scheme != 'https' or not host or parsed.username or parsed.password:
            raise ValueError('City feed must use HTTPS without URL credentials')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('IP literals are not allowed for city feeds')
        if host in ('localhost',) or host.endswith('.local') or host not in hosts:
            raise ValueError('Feed host is not in the configured allowlist')
        if parsed.port not in (None, 443):
            raise ValueError('Only standard HTTPS ports are supported')
        if not parsed.path.lower().endswith(('.csv', '.json')):
            raise ValueError('Feed URL must point to a CSV or JSON file')
        self.url = url
        self.fmt = parsed.path.lower().rsplit('.', 1)[-1]
        self.client = client

    def fetch(self):
        client = self.client or httpx.Client(timeout=20, follow_redirects=False,
                        headers={'User-Agent': 'OstrovokResearchPilot/1.0 (curated city feed)'})
        try:
            with client.stream('GET', self.url) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError('City feed did not return HTTP 200')
                mime = response.headers.get('content-type', '').split(';', 1)[0].lower()
                allowed = ('text/csv', 'application/csv', 'text/plain') if self.fmt == 'csv' else ('application/json', 'text/json')
                if mime and mime not in allowed:
                    raise ValueError('Unexpected feed media type')
                pieces, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('City feed exceeds size limit')
                    pieces.append(chunk)
            return parse_notices(b''.join(pieces).decode('utf-8-sig'), self.fmt)
        finally:
            if self.client is None:
                client.close()


def main():
    parser = argparse.ArgumentParser(description='Fetch trusted configured city CSV/JSON feed')
    parser.add_argument('--apply', action='store_true', help='Stage changed notices for review')
    args = parser.parse_args()
    if os.getenv('OSTROVOK_LIVE_SOURCES', '').lower() != 'true':
        parser.error('OSTROVOK_LIVE_SOURCES=true is required for network access')
    url = os.getenv('OSTROVOK_CITY_FEED_URL', '')
    allowlist = os.getenv('OSTROVOK_CITY_FEED_HOSTS', '')
    try:
        notices = CityFeedClient(url, allowlist).fetch()
        initialize()
        with session() as db:
            report = import_notices(db, notices, apply=args.apply)
    except (ValueError, httpx.HTTPError, UnicodeError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
