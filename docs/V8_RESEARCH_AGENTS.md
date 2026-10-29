# V8 — City events, building identity, geo-tagged photos

## What is actually implemented

Three research paths extend the v7 Evidence Store:

1. **City records:** administrator can submit a construction, street closure or festival notice with coordinates, dates and the exact HTTPS source. Records are initially `pending`. An operator reviews the original and marks the record `reviewed` or `rejected`. Only reviewed records are considered by the traveler API. The date-scoped calculation uses great-circle distance and adds the event footprint radius to the requested search radius. The UI surfaces a dated warning with a source link. No record never implies the absence of construction. A reviewed notice describes **reported/scheduled works**, not confirmed on-site activity.
2. **Building identity:** a Wikidata QID is linked to a specific physical building only through an explicit reference and administrative approval. The metadata inspection action uses `wbgetentities` (labels, descriptions, Wikipedia links, P571). Pending metadata is a candidate, **not a confirmed historical use**. A subsequent existing enrichment job for `building_year` can use the reviewed QID. Updating the QID invalidates identity approval.
3. **Geo-tagged imagery:** Commons `generator=geosearch` retrieves file pages near a hotel's coordinates. API data includes title, geographic distance, file URL, thumbnail and available license short name. Candidates are stored but never shown publicly until individually reviewed. Even a reviewed street photograph is **not** proof of a room view, balcony or rooftop terrace.

These modules use the existing `source_records` and `audit_events`, and add `urban_records`, `building_identities` and `media_candidates`. They work on SQLite and PostgreSQL (migration `sql/004_research_v8.sql`).

## Local startup

```bash
sh start.sh
```

On Windows use `start.ps1`. Open `http://127.0.0.1:8090` and `http://127.0.0.1:8090/docs`. This mode listens on loopback only. The scripts set `OSTROVOK_ALLOW_LOCAL_WRITE=true` for research demonstrations. **Never expose the local-write configuration to the internet.**

All municipal data must be entered from a verifiable source. An example below is a **synthetic API contract demonstration, not a real city notice**. No city events are seeded by default.

## Scenario A — city record and travel dates

1. Get the hotel ID from `GET /api/v2/intelligence/hotels`. Ensure the hotel has sourced coordinates; otherwise matching is impossible.
2. In Swagger call `POST /api/v2/intelligence/urban/records` with an event:

```json
{
  "category": "construction",
  "title": "Пример: ремонт участка дороги",
  "description": "Учебная запись для проверки геофильтра, не реальная стройка",
  "starts_on": "2026-11-01",
  "ends_on": "2026-11-09",
  "lat": 43.5701,
  "lon": 39.7322,
  "radius_m": 100,
  "source_url": "https://example.org/demo-only",
  "source_reference": "test-fixture-001",
  "provider": "manual-demo"
}
```

3. Check `GET /api/v2/intelligence/urban/pending`. A pending record must not appear in customer results.
4. Open the source and verify information. For demo records, mark them only within a controlled test environment. Use `POST /api/v2/intelligence/urban/records/{id}/review` with `{"status":"reviewed"}`.
5. Search with travel dates:

```json
{
  "query": "Тихий отель рядом с морем",
  "arrival": "2026-11-02",
  "departure": "2026-11-06"
}
```

6. Examine `urban_context`, `caveats` and `GET /hotels/{hotel_id}/surroundings?arrival=2026-11-02&departure=2026-11-06` (prefix `/api/v2/intelligence`). Events outside dates or radius are excluded. The status is `unknown` when no relevant reviewed records are available, not `verified: false`.
7. For production import, a genuine official municipal source connector, coverage mapping and change detection must still be implemented. Manual submissions are not automated scraping.

## Scenario B — Wikidata identity and building year

1. Submit `POST /api/v2/intelligence/hotels/{hotel_id}/building/identity` with the correct Wikidata QID, an independent HTTPS document proving the specific building identity and a short review note.
2. When `OSTROVOK_LIVE_SOURCES=true`, inspect the candidate via `POST /api/v2/intelligence/hotels/{hotel_id}/building/inspect`. This fetches metadata, saves a source record and returns `building_history_fact_created=false`.
3. The admin compares address, building photo, historical name and identity references; then calls `POST /api/v2/intelligence/hotels/{hotel_id}/building/identity/review` with `{"approved":true}` or `{"approved":false}`.
4. Only after approval submit `/jobs/enrich`, with `mode=live` and `feature_keys=["building_year"]` plus a unique `Idempotency-Key`. The connector reads P571. It does **not** promote a Wikidata description to proven building history.
5. `GET /hotels/{hotel_id}/building/identity` returns status, linked item and source. For `building_history`, use the existing hotelier process or independent archival research.

## Scenario C — Wikimedia Commons images

1. Ensure a hotel has non-synthetic sourced coordinates (`/hotels/{hotel_id}/geo/status`).
2. Set `OSTROVOK_LIVE_SOURCES=true` in the server environment and call protected `POST /hotels/{hotel_id}/media/discover`. This queries Commons' public MediaWiki Action API near that point. A failure to reach Commons is an error, not a fabricated result.
3. Review candidates via protected `GET /hotels/{hotel_id}/media/candidates`. Compare the image and geographic context with the actual hotel, check the file's Commons page and license. Approve with `POST /hotels/{hotel_id}/media/{candidate_id}/review`, body `{"approved":true}`.
4. Public `GET /hotels/{hotel_id}/media` now returns only reviewed file pages, not pending images. The Mini App displays thumbnails and direct attribution links. The response always says `view_confirmed=false`.

## API access model

Read endpoints for travelers: `GET /hotels/{id}/surroundings`, `GET /hotels/{id}/building/identity`, `GET /hotels/{id}/media`, plus existing search/profile/evidence. Administrative writes, review queues and live discovery require `OSTROVOK_API_TOKEN` (service-to-service Bearer) outside loopback demo. A Telegram traveler token is not an admin credential.

Never place the service token in browser code. The protected administration workflows currently run through Swagger/service API; a full administrator interface and fine-grained role management remain future work.

## Validation and limitations

```bash
python -m pytest -q
node --check web/app.js
```

Real network calls to Overpass, Commons, Wikidata, and Valhalla could not be made from the build environment. Tests use injected HTTP fixtures and a local SQLite database. PostgreSQL integration requires Docker or an existing DB; its integration test is skipped without the configured DSN.

No municipal records are bundled as verified. Missing municipal coverage, room orientation, sunset visibility, acoustic measurements and rooftop access remain unresolved and should trigger questions or further source research. This is **not** a real-time monitoring service yet.
