import uuid
from .engine import create_job, run_job
from .registry import Registry
from .store import get_evidence, get_profile, initialize, list_hotels, session


def main():
    initialize()
    registry = Registry()
    with session() as db:
        hotel = list_hotels(db)[0]
        job_id, created = create_job(db, hotel['id'], ['coast_straight_m', 'sunset_view', 'dogs_allowed'],
                                     'demo', 'demo-initial-enrichment', registry)
    if created:
        run_job(job_id)
    with session() as db:
        fact = get_profile(db, hotel['id'], registry)['facts']['coast_straight_m']
        evidence = get_evidence(db, hotel['id'], 'coast_straight_m')
    print(f"{hotel['name']}\ncoast_straight_m={fact['value']} m\nstatus={fact['status']}")
    print('provenance=' + evidence[0]['provenance'] if evidence else 'No evidence')


if __name__ == '__main__':
    main()
