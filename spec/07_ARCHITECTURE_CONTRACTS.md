# Архитектурные контракты и схема данных

Это **предлагаемая архитектура**, а не описание уже работающих модулей репозитория.

## 1. Bounded contexts и зоны ответственности

| Контекст | Владеет | Не владеет |
|---|---|---|
| Existing Go API (BFF) | Telegram-сессией, профилями, свайпами, пользовательскими рекомендациями, маршрутизацией запросов | provenance и решением конфликтов |
| Catalog Mapping | canon hotel_id, external IDs, дедупликацией, координатами | пользовательскими аккаунтами |
| Ingestion | коннекторами, snapshots, обработкой 429/timeout, лицензиями | признанием истины |
| Evidence | source records, assertions, claims version, audit | генерацией красивых формулировок |
| Geo Engine | геометрией, маршрутами, интервальными пространственными событиями | оценками модели |
| Agent Orchestrator | выбором зарегистрированных инструментов, постановкой jobs | произвольным исполнением LLM-кода |
| Resolution | resolved facts, конфликты, политики TTL, причины решений | удалением исходных доказательств |
| Hotelier | вопросами, ответами, подтверждениями, принадлежностью отеля | настройкой глобальной системы |
| Search | разбором запроса, match reasons, объяснениями | синтетическими «фактами» |

## 2. Предлагаемая физическая организация

```text
ostrovok/
├── backend/                      # исходный Go API и webapp
├── analytics/                    # исходные SQL и аналитика
├── intelligence/
│   ├── api/                      # REST, схемы, auth middleware
│   ├── domain/                   # model, validators, resolution, policies
│   ├── connectors/               # osm, wikidata, commons, city, hotelier
│   ├── geo/                      # distances, routes, time windows
│   ├── agents/                   # planner, extraction, verification
│   ├── jobs/                     # очередь, scheduler, refresh
│   ├── search/                   # query parser, structured filters, explanations
│   ├── registry/                 # versioned YAML feature definitions
│   ├── migrations/               # intelligence schema в той же PostgreSQL
│   ├── seeds/                    # реальные snapshot и явно тестовые данные
│   └── tests/                    # unit, integration, contract, e2e
├── docs/
│   ├── API_INTELLIGENCE.md
│   ├── DATA_PROVENANCE.md
│   ├── REPRODUCIBILITY.md
│   └── EVALUATION.md
└── docker-compose.yml
```

Построение пути после фактического аудита репозитория может быть уточнено. Важно сохранить существующие эндпоинты `/v1/*`, не создавать отдельное приложение с самостоятельным каталогом.

## 3. PostgreSQL / PostGIS: logical schema

```text
core.hotels
  id, name, address, lat, lon, ...

intelligence.hotel_external_ids
  hotel_id, provider, external_id, matched_by, match_confidence, reviewed_at

intelligence.feature_definitions
  feature_key, version, scope, data_type, unit, ttl_days, strategy, registry_sha

intelligence.source_records
  id, provider, external_id, url, title, license, published_at,
  retrieved_at, valid_from, valid_to, checksum_sha256, raw_uri

intelligence.fact_assertions
  id, hotel_id, subject_type, subject_id, feature_key, feature_version,
  value_jsonb, unit, method, confidence, confidence_basis,
  source_record_id, observed_at, retrieved_at, valid_from, valid_to,
  context_jsonb, algorithm_version, created_at

intelligence.resolved_facts
  id, hotel_id, subject_type, subject_id, feature_key, valid_from, valid_to,
  status, value_jsonb, resolution_reason, updated_at

intelligence.resolved_fact_assertions
  resolved_fact_id, assertion_id

intelligence.fact_conflicts
  id, hotel_id, feature_key, status, assertion_ids_jsonb, reviewer_id,
  reason, decided_at

intelligence.geo_features
  id, provider, category, geom GEOGRAPHY, valid_from, valid_to, raw_source_id

intelligence.enrichment_jobs
  id, hotel_id, mode, requested_feature_keys_jsonb, visit_from, visit_to,
  status, idempotency_key, created_at, updated_at

intelligence.job_attempts
  id, job_id, tool_key, attempt, status, started_at, ended_at, error_code

intelligence.hotelier_questions
  id, hotel_id, feature_keys_jsonb, status, question, reason, created_at

intelligence.hotelier_answers
  id, question_id, hotelier_user_id, raw_text, created_at, status

intelligence.extraction_candidates
  id, answer_id, feature_key, value_jsonb, context_jsonb,
  raw_excerpt, confidence, status

intelligence.evaluation_items
  id, hotel_id, feature_key, context_jsonb, expected_value_jsonb,
  label_status, reviewer_id, dataset_split

intelligence.audit_events
  id, actor_type, actor_id, action, object_type, object_id,
  previous_digest, new_digest, reason, created_at
```

Индексы: `(hotel_id, feature_key, subject_type, subject_id)` для assertions и resolved, `GIST(geom)` для geo_features, уникальный `(provider, external_id)` для идентичности, уникальный `(idempotency_key, actor_id)` для jobs, BTREE для `retrieved_at`, `valid_to` и `status`. Предусмотреть частичные/временные индексы при росте данных.

**Граница точности:** `resolved_fact` — материализованное производное состояние, `fact_assertion` — неизменяемое свидетельство. Пересчёт может обновить/версионировать resolved, но не редактирует историю источников.

## 4. Контракт инструмента SourceConnector

```python
class SourceConnector(Protocol):
    key: str
    supported_features: set[str]

    async def probe(self) -> SourceHealth: ...
    async def fetch(self, hotel: HotelRef, context: EnrichmentContext) -> list[SourceRecord]: ...
    async def extract(self, record: SourceRecord, features: list[str]) -> list[FactCandidate]: ...
```

Требования: повторяемый `source_id`, обновляемый cursor/ETag (если доступен), явный `license`, rate limiting, время проверки, переход в `unavailable`, если источник отключён. Коннектор не записывает напрямую в `resolved_fact`.

## 5. Контракт вычислителя GeoCalculator

```python
class GeoCalculator(Protocol):
    key: str
    version: str

    async def calculate(
        self,
        subject: HotelOrRoom,
        inputs: list[SourceRecord],
        context: EnrichmentContext,
    ) -> list[FactCandidate]: ...
```

Результат содержит `algorithm_version`, `input_source_ids`, единицы, допущения, геометрию (при необходимости), `method=derived`. Ошибка роутера → `unknown` с reason `route_unavailable`, а не оценка от геодистанции с тем же ключом.

## 6. Контракт AgentPlanner и структурированной верификации

```python
class AgentPlanner(Protocol):
    async def plan(self, hotel_id: str, feature_keys: list[str], context: EnrichmentContext) -> Plan: ...

class CandidateVerifier(Protocol):
    async def verify(self, candidate: FactCandidate) -> VerificationResult: ...

class FactResolver(Protocol):
    async def resolve(self, hotel_id: str, feature_key: str, context: ResolutionContext) -> ResolvedFact: ...
```

Нормализация времени, единиц, отрицаний и контекстов — до разрешения факта. Выход LLM никогда не направляется прямо в `resolved_fact`. У планировщика allowlist зарегистрированных инструментов и предел шагов.

## 7. Версионирование событий (для очереди и Go-интеграции)

Общий envelope:

```json
{
  "event_id": "43c81b48-31d1-4f69-abce-e0dd49da8e27",
  "event_type": "intelligence.fact.resolved.v1",
  "occurred_at": "2026-10-26T12:00:00Z",
  "correlation_id": "37c9a584-a32a-4bac-9781-39cb759a97bd",
  "aggregate_id": "8c7ae201-8d53-4530-9ea8-fc3ed2d5edc9",
  "payload": {
    "hotel_id": "8c7ae201-8d53-4530-9ea8-fc3ed2d5edc9",
    "feature_key": "dogs_allowed",
    "status": "reported",
    "assertion_ids": ["9ca2de13-f855-4493-a2aa-264c8b7e2d08"]
  }
}
```

Обязательные события: `enrichment.queued.v1`, `enrichment.completed.v1`, `evidence.added.v1`, `fact.conflict_detected.v1`, `fact.resolved.v1`, `hotelier.question_created.v1`, `hotelier.answer_received.v1`, `fact.stale.v1`. Доставка может быть at-least-once; consumers идемпотентны по `event_id`.

## 8. API интеграция с существующим Go

- **Не ломать** `GET /health`, `GET /v1/offers`, `POST /v1/swipes`, `POST /v1/agent/chat`, `GET /v1/profile`, `GET /v1/saved`.
- В Go `POST /v1/agent/chat` выполнить intent routing только для запросов на подтверждаемые характеристики отелей; делегировать в `POST /api/v2/intelligence/search` с серверным `request_id` и аутентифицированным principal.
- `POST /v1/swipes` продолжает обновлять предпочтения, но не меняет доказательства/решения факт-сервиса.
- Исходные ID `offer_id` связать через `hotel_external_ids`; не предполагать, что ID каталога равен числовому ID учебной базы.
- В production внутренние сервисы доверяют только сервисной аутентификации/проверенной сессии; заголовок `X-User-Id` от браузера недостаточен.
- OpenAPI находится в `04_API_CONTRACT.openapi.yaml` — это **проектные** маршруты, которые ещё предстоит реализовать и согласовать с исходным Go-кодом.

## 9. Ключевые транзакции

### Исследование

`POST enrich → 202/job_id → queue → connector fetch → immutable source_record → candidate → verify → assertion → resolver → resolved_fact/conflict → event → search read`.

### Ответ отельера

`question → free_text answer → multiple extraction candidates → review/confirmation → assertions → conflict/resolution → audit → search refresh`.

### Поиск

`query → intent parsing → hard/soft features → resolved values for visit_window → rank + explain → references assertion/source`.

## 10. Ошибки и обратная совместимость

Общий контракт ошибок:

```json
{
  "error": {
    "code": "SOURCE_RATE_LIMITED",
    "message": "Источник временно ограничил запросы",
    "retryable": true,
    "retry_after_seconds": 60,
    "request_id": "37c9a584-a32a-4bac-9781-39cb759a97bd"
  }
}
```

Стандартные коды: `AUTH_REQUIRED` (401), `FORBIDDEN` (403), `NOT_FOUND` (404), `INVALID_FEATURE` (422), `SOURCE_RATE_LIMITED` (job-level 429), `CONFLICT_REQUIRES_REVIEW` (409), `DEPENDENCY_UNAVAILABLE` (503). Старые маршруты остаются рабочими; новые контракты версионированы `/api/v2/intelligence`.

## 11. Security, privacy, legal

- Проверка Telegram `initData` server side, защита от replay через auth_date + expiry, сессии; `initDataUnsafe` не доверять.
- RBAC + ownership для отельеров, reviewer/admin отдельно.
- Доступ к произвольным URL в агентах запрещён: только whitelisted endpoints, запрет SSRF, ограничение исходящих запросов.
- Сырые ответы отельеров могут содержать PII: хранить минимально нужный объём, ретеншн и права доступа.
- Секреты только в переменных окружения/secret manager, `.env.example` не содержит реальных ключей.
- Ссылки, гео-фото, карты и тексты — с указанием разрешённой лицензии/атрибуции.
