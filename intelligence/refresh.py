import argparse
import json
import os
import uuid
from datetime import datetime, timezone

from .engine import create_job, run_job
from .registry import Registry
from .store import get_profile, list_hotels, session

GEO_FEATURES = ('coast_straight_m', 'beach_route_m', 'beach_walk_minutes')


def due_geo_tasks(db, registry, force=False, hotel_id=None, limit=3):
    tasks = []
    for hotel in list_hotels(db):
        if hotel_id and hotel['id'] != hotel_id:
            continue
        if hotel['lat'] is None or hotel['lon'] is None or hotel['coord_status'] == 'synthetic_fixture':
            continue
        profile = get_profile(db, hotel['id'], registry)
        keys = [key for key in GEO_FEATURES if force or profile['facts'][key]['status'] in ('unknown', 'stale')]
        if keys:
            tasks.append({'hotel_id': hotel['id'], 'name': hotel['name'], 'feature_keys': keys})
        if len(tasks) >= limit:
            break
    return tasks


def main():
    parser = argparse.ArgumentParser(description='Review or refresh sourced geographic hotel features')
    parser.add_argument('--dry-run', action='store_true', help='Show due tasks without contacting providers')
    parser.add_argument('--live', action='store_true', help='Actually query OSM and pedestrian router')
    parser.add_argument('--force', action='store_true', help='Recheck existing facts')
    parser.add_argument('--hotel-id', help='Limit to one hotel UUID')
    parser.add_argument('--limit', type=int, default=2, help='Maximum hotels per run (1..10)')
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        parser.error('--limit must be between 1 and 10')
    if args.live and os.getenv('OSTROVOK_LIVE_SOURCES', '').lower() != 'true':
        parser.error('Set OSTROVOK_LIVE_SOURCES=true before live refresh')
    registry = Registry()
    with session() as db:
        tasks = due_geo_tasks(db, registry, args.force, args.hotel_id, args.limit)
    report = {'dry_run': not args.live, 'tasks': tasks, 'results': []}
    if args.live:
        for task in tasks:
            key = f'geo-refresh:{task["hotel_id"]}:{uuid.uuid4()}' if args.force else (
                  f'geo-refresh:{task["hotel_id"]}:{datetime.now(timezone.utc):%Y%m%d%H}')
            with session() as db:
                job_id, created = create_job(db, task['hotel_id'], task['feature_keys'], 'live', key, registry)
            detail = run_job(job_id) if created else None
            report['results'].append({'hotel_id': task['hotel_id'], 'job_id': job_id,
                                      'created': created, 'steps': detail})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
