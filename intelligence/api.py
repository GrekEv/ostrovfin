import hmac
import os
import uuid
from datetime import datetime, timezone, date
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field
from .engine import create_job, plan, run_job
from .urban import submit_record, review_record, pending_records, surroundings
from .heritage import building_identity, submit_identity, review_identity, inspect_identity
from .media import CommonsGeosearch, save_candidates, list_media, review_media
from .operator import overview as operator_overview, queue as operator_queue, audit_log, refresh_plan
from .geoagent import OverpassPlaces
from .georeview import approve_candidate, discover, list_candidates, recent_changes
from .hotelier import answer_question, confirm_answer, create_question
from .registry import Registry
from .search import search
from .telegram_auth import TelegramAuthError, verified_hotelier
from .store import get_evidence, get_profile, initialize, iso_now, list_hotels, one, session, stats

registry = Registry()


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title='Ostrovok Intelligence — Stage 1', version='1.3.0', lifespan=lifespan)
PREFIX = '/api/v2/intelligence'


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    region: str = 'sochi-pilot'
    limit: int = Field(default=10, ge=1, le=50)
    hard_filters: dict = Field(default_factory=dict)
    include_demo: bool = False
    arrival: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    departure: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class CoordinateRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    source_url: str = Field(pattern=r'^https://', min_length=12, max_length=2000)
    source_reference: str = Field(min_length=3, max_length=250)


class ApproveGeoRequest(BaseModel):
    candidate_id: str = Field(min_length=32, max_length=32, pattern=r'^[a-f0-9]{32}$')


class EnrichRequest(BaseModel):
    hotel_id: uuid.UUID
    feature_keys: list[str] = Field(min_length=1)
    mode: str = 'demo'


class QuestionRequest(BaseModel):
    hotel_id: uuid.UUID
    feature_keys: list[str] = Field(min_length=1)
    question: str = Field(min_length=8)
    reason: str = 'unknown'


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class ConfirmRequest(BaseModel):
    candidate_ids: list[uuid.UUID] = Field(min_length=1)



class UrbanRecordRequest(BaseModel):
    category: str
    title: str = Field(min_length=5, max_length=180)
    description: str = Field(min_length=5, max_length=1000)
    starts_on: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    ends_on: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: float = Field(default=0, ge=0, le=2000)
    source_url: str = Field(pattern=r'^https://', max_length=2000)
    source_reference: str = Field(min_length=3, max_length=180)
    provider: str = Field(min_length=3, max_length=80)


class ReviewStatusRequest(BaseModel):
    status: str


class BuildingIdentityRequest(BaseModel):
    qid: str = Field(pattern=r'^Q[1-9][0-9]*$')
    reference_url: str = Field(pattern=r'^https://', max_length=2000)
    note: str = Field(min_length=8, max_length=500)


class DecisionRequest(BaseModel):
    approved: bool

def local_demo(request):
    return (os.getenv('OSTROVOK_MODE', 'demo') == 'demo'
            and os.getenv('OSTROVOK_ALLOW_LOCAL_WRITE', 'false').lower() == 'true'
            and request.client is not None
            and request.client.host in ('testclient', '127.0.0.1', '::1')
            and not os.getenv('OSTROVOK_API_TOKEN'))


def telegram_hotelier(request):
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('tma '):
        return None
    try:
        _, hotel_id = verified_hotelier(auth[4:])
    except TelegramAuthError as error:
        raise HTTPException(401, str(error)) from error
    if not hotel_id:
        raise HTTPException(403, 'No hotel assignment for Telegram user')
    return str(hotel_id)


def require_role(request: Request, role: str):
    if local_demo(request):
        request.state.actor_hotel_id = None
        return
    hotel_id = telegram_hotelier(request) if role == 'hotelier' else None
    if hotel_id:
        request.state.actor_hotel_id = hotel_id
        return
    token = os.getenv('OSTROVOK_API_TOKEN', '')
    bearer = request.headers.get('Authorization', '')
    if not token or not hmac.compare_digest(bearer, f'Bearer {token}'):
        raise HTTPException(401, 'Authentication required')
    if role == 'hotelier':
        if os.getenv('OSTROVOK_ALLOW_SERVICE_HOTELIER', 'false').lower() != 'true':
            raise HTTPException(403, 'Hotelier role requires Telegram identity')
        hotel_id = request.headers.get('X-Actor-Hotel-Id', '')
        if not hotel_id:
            raise HTTPException(403, 'Missing trusted hotel ownership context')
        request.state.actor_hotel_id = hotel_id


def ensure_ownership(request, hotel_id):
    if not local_demo(request) and request.state.actor_hotel_id != str(hotel_id):
        raise HTTPException(403, 'Hotel does not belong to actor')


@app.get(PREFIX + '/me')
def current_actor(request: Request):
    if local_demo(request):
        with session() as db:
            hotels = list_hotels(db)
        demo_hotel = next((h['id'] for h in hotels if h['coord_status'] == 'synthetic_fixture'), None)
        return {'role': 'demo_hotelier' if demo_hotel else 'traveler', 'hotel_id': demo_hotel, 'demo': True}
    auth = request.headers.get('Authorization', '')
    if auth.startswith('tma '):
        try:
            user, hotel_id = verified_hotelier(auth[4:])
        except TelegramAuthError:
            raise HTTPException(401, 'Invalid Telegram session')
        return {'role': 'hotelier' if hotel_id else 'traveler', 'hotel_id': hotel_id,
                'display_name': user.get('first_name', ''), 'demo': False}
    return {'role': 'traveler', 'hotel_id': None, 'demo': False}


@app.get('/health')
def health():
    return {'status': 'ok', 'component': 'intelligence', 'mode': os.getenv('OSTROVOK_MODE', 'demo'),
            'storage': os.getenv('OSTROVOK_STORAGE', 'sqlite')}


@app.get('/ready')
def ready():
    try:
        with session() as db:
            row = db.execute('SELECT COUNT(*) FROM hotels').fetchone()
            return {'status': 'ready', 'storage': os.getenv('OSTROVOK_STORAGE', 'sqlite'),
                    'hotels': row[0]}
    except Exception:
        raise HTTPException(503, 'Storage unavailable')


@app.get(PREFIX + '/hotels')
def hotels():
    with session() as db:
        return {'hotels': list_hotels(db)}


@app.get(PREFIX + '/features')
def features():
    return {'features': registry.public()}


@app.get(PREFIX + '/hotels/{hotel_id}/profile')
def profile(hotel_id: uuid.UUID):
    with session() as db:
        data = get_profile(db, str(hotel_id), registry)
        if not data:
            raise HTTPException(404, 'Hotel not found')
        return {'hotel_id': str(hotel_id), 'name': data['hotel']['name'], 'hotel': data['hotel'],
                'facts': [{'feature_key': key, **value} for key, value in data['facts'].items()]}


@app.get(PREFIX + '/hotels/{hotel_id}/evidence')
def evidence(hotel_id: uuid.UUID, feature_key: str):
    try:
        registry.get(feature_key)
    except KeyError:
        raise HTTPException(422, 'Invalid feature')
    with session() as db:
        if not one(db, str(hotel_id)):
            raise HTTPException(404, 'Hotel not found')
        return {'assertions': get_evidence(db, str(hotel_id), feature_key)}


@app.post(PREFIX + '/search')
def search_endpoint(body: SearchRequest):
    if body.region != 'sochi-pilot':
        raise HTTPException(422, 'Region is not supported')
    try:
        with session() as db:
            if body.arrival is not None or body.departure is not None:
                if not body.arrival or not body.departure:
                    raise ValueError('Both arrival and departure are required')
                begin, end = date.fromisoformat(body.arrival), date.fromisoformat(body.departure)
                if end <= begin or (end - begin).days > 60:
                    raise ValueError('Invalid travel dates')
            response = search(db, registry, body.query, body.limit, body.hard_filters, body.include_demo)
            if body.arrival is not None and body.departure is not None:
                for result in response['results']:
                    context = surroundings(db, result['hotel_id'], body.arrival, body.departure)
                    result['urban_context'] = context
                    if context['events']:
                        result['caveats'].append('События поблизости в указанные даты: ' +
                                                 ', '.join(e['title'] for e in context['events'][:3]))
                    construction = [e for e in context['events'] if e['category'] == 'construction']
                    for criterion, match in zip(response['criteria'], result['matches']):
                        if criterion['feature_key'] == 'active_construction_nearby' and construction:
                            match['status'] = 'mismatch'
                            match['explanation'] = 'В проверенных объявлениях заявлены работы поблизости; фактическая активность не подтверждена'
                            result['eligible'] = False
                    if construction and any(c['feature_key'] == 'active_construction_nearby' for c in response['criteria']):
                        weight_total = sum(2 if c['priority'] == 'hard' else 1 for c in response['criteria'])
                        points = sum((2 if c['priority'] == 'hard' else 1) *
                                     {'match': 1, 'partial': .5}.get(m['status'], 0)
                                     for c, m in zip(response['criteria'], result['matches']))
                        result['score'] = round(100 * points / weight_total) if weight_total else None
                response['results'].sort(key=lambda r: (-r['eligible'], -(r['score'] or 0), r['name']))
            return response
    except (KeyError, ValueError) as error:
        raise HTTPException(422, str(error))


@app.post(PREFIX + '/hotels/{hotel_id}/geo/discover')
def discover_geolocation(hotel_id: uuid.UUID, request: Request):
    require_role(request, 'admin')
    if os.getenv('OSTROVOK_LIVE_SOURCES', 'false').lower() != 'true':
        raise HTTPException(403, 'Live sources are disabled')
    import httpx
    try:
        with session() as db:
            result = discover(db, str(hotel_id), OverpassPlaces(os.getenv('OVERPASS_URL', 'https://overpass-api.de/api/interpreter')))
            return {'candidates': result, 'needs_manual_review': True}
    except ValueError as error:
        raise HTTPException(404, str(error))
    except (httpx.HTTPError, OSError) as error:
        raise HTTPException(502, 'Geodata provider unavailable') from error


@app.get(PREFIX + '/hotels/{hotel_id}/geo/candidates')
def geo_candidates(hotel_id: uuid.UUID, request: Request):
    require_role(request, 'admin')
    with session() as db:
        try:
            return {'candidates': list_candidates(db, str(hotel_id))}
        except ValueError as error:
            raise HTTPException(404, str(error))


@app.post(PREFIX + '/hotels/{hotel_id}/geo/approve')
def approve_geo(hotel_id: uuid.UUID, body: ApproveGeoRequest, request: Request):
    require_role(request, 'admin')
    with session() as db:
        try:
            return approve_candidate(db, str(hotel_id), body.candidate_id)
        except ValueError as error:
            raise HTTPException(404, str(error))


@app.get(PREFIX + '/hotels/{hotel_id}/geo/status')
def geo_status(hotel_id: uuid.UUID):
    with session() as db:
        hotel = one(db, str(hotel_id))
        if not hotel:
            raise HTTPException(404, 'Hotel not found')
        profile = get_profile(db, str(hotel_id), registry)
        keys = ('coast_straight_m', 'beach_route_m', 'beach_walk_minutes')
        return {'hotel_id': str(hotel_id), 'lat': hotel['lat'], 'lon': hotel['lon'],
                'coordinate_status': hotel['coord_status'],
                'facts': {key: profile['facts'][key] for key in keys},
                'notice': 'Маршрут до OSM-точки пляжа; доступность входа и ограничения нужно проверять отдельно.'}


@app.get(PREFIX + '/hotels/{hotel_id}/changes')
def changes(hotel_id: uuid.UUID, request: Request):
    require_role(request, 'admin')
    with session() as db:
        if not one(db, str(hotel_id)):
            raise HTTPException(404, 'Hotel not found')
        return {'changes': recent_changes(db, str(hotel_id))}


@app.post(PREFIX + '/hotels/{hotel_id}/coordinates')
def sourced_coordinates(hotel_id: uuid.UUID, body: CoordinateRequest, request: Request):
    require_role(request, 'admin')
    from .connectors import Source
    from .store import put_source, audit
    with session() as db:
        hotel = one(db, str(hotel_id))
        if not hotel:
            raise HTTPException(404, 'Hotel not found')
        source = Source('coordinate_review', body.source_reference, body.source_url,
                        {'hotel_id': str(hotel_id), 'lat': body.lat, 'lon': body.lon},
                        iso_now(), 'reviewed_external', 'not_assessed')
        sid = put_source(db, source)
        db.execute('UPDATE hotels SET lat=?,lon=?,coord_status=? WHERE id=?',
                   (body.lat, body.lon, 'sourced_reviewed', str(hotel_id)))
        db.execute("DELETE FROM resolved_facts WHERE hotel_id=? AND feature_key IN ('coast_straight_m','beach_route_m','beach_walk_minutes','dog_park_walk_minutes')", (str(hotel_id),))
        audit(db, 'hotel.coordinates_updated', str(hotel_id), {'source_id': sid})
        return {'hotel_id': str(hotel_id), 'coord_status': 'sourced_reviewed', 'source_id': sid}


@app.post(PREFIX + '/jobs/enrich', status_code=202)
def enrich(body: EnrichRequest, request: Request, background_tasks: BackgroundTasks,
           idempotency_key: str = Header(..., alias='Idempotency-Key')):
    require_role(request, 'admin')
    if body.mode not in ('demo', 'live'):
        raise HTTPException(422, 'Invalid mode')
    if body.mode == 'live' and os.getenv('OSTROVOK_LIVE_SOURCES', 'false').lower() != 'true':
        raise HTTPException(403, 'Live sources are disabled')
    try:
        with session() as db:
            job_id, created = create_job(db, str(body.hotel_id), body.feature_keys, body.mode,
                                         idempotency_key, registry)
    except KeyError as error:
        raise HTTPException(422, f'Unknown feature: {error}')
    except ValueError as error:
        raise HTTPException(409 if 'mismatch' in str(error) else 422, str(error))
    if created:
        background_tasks.add_task(run_job, job_id)
    return {'job_id': job_id, 'status': 'queued' if created else 'existing', 'created': created}


@app.get(PREFIX + '/jobs/{job_id}')
def job_status(job_id: uuid.UUID):
    import json
    with session() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (str(job_id),)).fetchone()
        if not row:
            raise HTTPException(404, 'Job not found')
        return {'job_id': row['id'], 'status': row['status'], 'steps': json.loads(row['details_json']),
                'created_at': row['created_at'], 'updated_at': row['updated_at']}


@app.post(PREFIX + '/hotelier/questions', status_code=201)
def new_question(body: QuestionRequest, request: Request):
    require_role(request, 'admin')
    if body.reason not in ('unknown', 'stale', 'conflicted', 'low_confidence'):
        raise HTTPException(422, 'Invalid reason')
    try:
        with session() as db:
            question_id = create_question(db, str(body.hotel_id), body.feature_keys,
                                          body.question, body.reason, registry)
            return {'question_id': question_id, 'status': 'open'}
    except KeyError as error:
        raise HTTPException(422, f'Unknown feature: {error}')
    except ValueError as error:
        raise HTTPException(404, str(error))


@app.get(PREFIX + '/hotelier/hotels/{hotel_id}/questions')
def questions(hotel_id: uuid.UUID, request: Request):
    require_role(request, 'hotelier')
    ensure_ownership(request, hotel_id)
    import json
    with session() as db:
        items = [dict(q) for q in db.execute('SELECT * FROM questions WHERE hotel_id=? ORDER BY created_at', (str(hotel_id),))]
        for item in items:
            item['feature_keys'] = json.loads(item['feature_keys'])
        return {'questions': items}


@app.post(PREFIX + '/hotelier/questions/{question_id}/answer')
def answer(question_id: uuid.UUID, body: AnswerRequest, request: Request):
    require_role(request, 'hotelier')
    with session() as db:
        question = db.execute('SELECT * FROM questions WHERE id=?', (str(question_id),)).fetchone()
        if not question:
            raise HTTPException(404, 'Question not found')
        ensure_ownership(request, question['hotel_id'])
        try:
            answer_id, candidates = answer_question(db, str(question_id), body.text, registry)
            return {'answer_id': answer_id, 'status': 'needs_confirmation', 'candidates': candidates}
        except ValueError as error:
            raise HTTPException(409, str(error))


@app.post(PREFIX + '/hotelier/answers/{answer_id}/confirm')
def confirm(answer_id: uuid.UUID, body: ConfirmRequest, request: Request):
    require_role(request, 'hotelier')
    with session() as db:
        row = db.execute('''SELECT q.hotel_id FROM answers a
            JOIN questions q ON q.id=a.question_id WHERE a.id=?''', (str(answer_id),)).fetchone()
        if not row:
            raise HTTPException(404, 'Answer not found')
        ensure_ownership(request, row['hotel_id'])
        try:
            published = confirm_answer(db, str(answer_id), [str(x) for x in body.candidate_ids], registry)
            return {'status': 'confirmed', 'assertions': published}
        except ValueError as error:
            raise HTTPException(409, str(error))


@app.get(PREFIX + '/metrics')
def metrics():
    with session() as db:
        data = stats(db, registry)
        return {'snapshot_at': iso_now(), 'hotels_count': data['hotels'],
                'feature_coverage': data['coverage'], 'verified_share': data['verified_share'],
                'unknown_share': 1 - data['coverage'], 'conflicts_open': data['conflicts'],
                'hotelier_escalation_rate': None, 'labeled_items': 0, 'precision': None,
                'reason': data['accuracy_reason']}



@app.get(PREFIX + '/hotels/{hotel_id}/surroundings')
def hotel_surroundings(hotel_id: uuid.UUID, arrival: str = Query(pattern=r'^\d{4}-\d{2}-\d{2}$'),
                       departure: str = Query(pattern=r'^\d{4}-\d{2}-\d{2}$'),
                       radius_m: int = Query(default=750, ge=100, le=5000)):
    with session() as db:
        try:
            return surroundings(db, str(hotel_id), arrival, departure, radius_m)
        except ValueError as error:
            raise HTTPException(422 if one(db, str(hotel_id)) else 404, str(error))


@app.post(PREFIX + '/urban/records', status_code=201)
def urban_submit(body: UrbanRecordRequest, request: Request):
    require_role(request, 'admin')
    with session() as db:
        try:
            record_id = submit_record(db, body.model_dump())
            return {'id': record_id, 'status': 'pending', 'published': False}
        except ValueError as error:
            raise HTTPException(422, str(error))


@app.get(PREFIX + '/urban/pending')
def urban_pending(request: Request):
    require_role(request, 'admin')
    with session() as db:
        return {'records': pending_records(db)}


@app.post(PREFIX + '/urban/records/{record_id}/review')
def urban_review(record_id: str, body: ReviewStatusRequest, request: Request):
    require_role(request, 'admin')
    with session() as db:
        try:
            return review_record(db, record_id, body.status)
        except ValueError as error:
            raise HTTPException(422 if 'status' in str(error) else 404, str(error))


@app.get(PREFIX + '/hotels/{hotel_id}/building/identity')
def hotel_building_identity(hotel_id: uuid.UUID):
    with session() as db:
        try:
            return building_identity(db, str(hotel_id))
        except ValueError as error:
            raise HTTPException(404, str(error))


@app.post(PREFIX + '/hotels/{hotel_id}/building/identity')
def building_submit(hotel_id: uuid.UUID, body: BuildingIdentityRequest, request: Request):
    require_role(request, 'admin')
    with session() as db:
        try:
            return submit_identity(db, str(hotel_id), body.qid, body.reference_url, body.note)
        except ValueError as error:
            raise HTTPException(404 if 'hotel' in str(error) else 422, str(error))


@app.post(PREFIX + '/hotels/{hotel_id}/building/inspect')
def building_inspect(hotel_id: uuid.UUID, request: Request):
    require_role(request, 'admin')
    if os.getenv('OSTROVOK_LIVE_SOURCES', 'false').lower() != 'true':
        raise HTTPException(403, 'Live sources are disabled')
    import httpx
    with session() as db:
        try:
            return inspect_identity(db, str(hotel_id))
        except ValueError as error:
            raise HTTPException(422, str(error))
        except (httpx.HTTPError, OSError):
            raise HTTPException(502, 'Wikidata unavailable')


@app.post(PREFIX + '/hotels/{hotel_id}/building/identity/review')
def building_review(hotel_id: uuid.UUID, body: DecisionRequest, request: Request):
    require_role(request, 'admin')
    with session() as db:
        try:
            return review_identity(db, str(hotel_id), body.approved)
        except ValueError as error:
            raise HTTPException(404, str(error))


@app.get(PREFIX + '/hotels/{hotel_id}/media')
def hotel_media(hotel_id: uuid.UUID):
    with session() as db:
        try:
            return {'media': list_media(db, str(hotel_id), public=True),
                    'view_confirmed': False,
                    'notice': 'A street-level geotag does not prove the view from any hotel room'}
        except ValueError as error:
            raise HTTPException(404, str(error))


@app.get(PREFIX + '/hotels/{hotel_id}/media/candidates')
def hotel_media_candidates(hotel_id: uuid.UUID, request: Request):
    require_role(request, 'admin')
    with session() as db:
        try:
            return {'media': list_media(db, str(hotel_id))}
        except ValueError as error:
            raise HTTPException(404, str(error))


@app.post(PREFIX + '/hotels/{hotel_id}/media/discover')
def discover_hotel_media(hotel_id: uuid.UUID, request: Request):
    require_role(request, 'admin')
    if os.getenv('OSTROVOK_LIVE_SOURCES', 'false').lower() != 'true':
        raise HTTPException(403, 'Live sources are disabled')
    import httpx
    with session() as db:
        hotel = one(db, str(hotel_id))
        if not hotel:
            raise HTTPException(404, 'Hotel not found')
        if hotel['lat'] is None or hotel['lon'] is None or hotel['coord_status'] == 'synthetic_fixture':
            raise HTTPException(409, 'Sourced coordinates required')
        try:
            result = CommonsGeosearch().discover(hotel['lat'], hotel['lon'])
            ids = save_candidates(db, str(hotel_id), result)
            return {'candidate_ids': ids, 'count': len(ids), 'status': 'pending_review',
                    'view_confirmed': False}
        except (httpx.HTTPError, KeyError, OSError, ValueError):
            raise HTTPException(502, 'Wikimedia Commons lookup unavailable')


@app.post(PREFIX + '/hotels/{hotel_id}/media/{candidate_id}/review')
def hotel_media_review(hotel_id: uuid.UUID, candidate_id: str, body: DecisionRequest, request: Request):
    require_role(request, 'admin')
    with session() as db:
        try:
            return review_media(db, str(hotel_id), candidate_id, body.approved)
        except ValueError as error:
            raise HTTPException(404, str(error))

@app.get(PREFIX + '/operator/overview')
def admin_overview(request: Request):
    require_role(request, 'admin')
    with session() as db:
        return operator_overview(db)


@app.get(PREFIX + '/operator/queue')
def admin_queue(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    require_role(request, 'admin')
    with session() as db:
        return operator_queue(db, limit)


@app.get(PREFIX + '/operator/audit')
def admin_audit(request: Request, limit: int = Query(default=40, ge=1, le=100)):
    require_role(request, 'admin')
    with session() as db:
        return {'events': audit_log(db, limit)}


@app.get(PREFIX + '/operator/refresh-plan')
def admin_refresh_plan(request: Request, limit: int = Query(default=10, ge=1, le=10)):
    require_role(request, 'admin')
    with session() as db:
        return refresh_plan(db, limit)


@app.get('/operator', include_in_schema=False)
def operator_app():
    return FileResponse(Path(__file__).resolve().parents[1] / 'web' / 'operator.html')


WEB_ROOT = Path(__file__).resolve().parents[1] / 'web'
app.mount('/app', StaticFiles(directory=str(WEB_ROOT)), name='miniapp-assets')

@app.get('/', include_in_schema=False)
def miniapp():
    return FileResponse(WEB_ROOT / 'index.html')
