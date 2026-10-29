# Подключение PostgreSQL/PostGIS и живых OSM-данных

## Вариант A — автономный запуск в Docker

Это самый простой способ проверить работу без доступа к исходной базе Go.

1. Установите Docker Engine / Docker Desktop.
2. Скопируйте `.env.example` в `.env`, укажите новый `POSTGRES_PASSWORD` и `OSTROVOK_API_TOKEN`.
3. Выполните в корне данного архива:

```bash
docker compose up -d --build
```

4. Откройте `http://127.0.0.1:8090/docs`.
5. Проверьте `GET /ready` — должен вернуться `storage: postgres` и `status: ready`.
6. Выполните POST `/api/v2/intelligence/jobs/enrich` с `mode=demo`, как в `docs/DEMO_WALKTHROUGH.md`. Укажите заголовки `Authorization: Bearer <OSTROVOK_API_TOKEN>` и `Idempotency-Key: demo-check-1`.

Сервер при старте применяет `sql/001_intelligence_postgis.sql` через `python -m intelligence.database`. Демопризнак берега рассчитывается по **синтетической** линии даже в PostGIS, и помечен соответствующим происхождением.

Для завершения работы:

```bash
docker compose down
```

Не запускайте `docker compose down -v`, если хотите сохранить свою базу.

## Вариант B — общая БД исходного Go API

1. Создайте дамп исходного PostgreSQL и проверьте возможность восстановления.
2. Убедитесь, что развернута та же мажорная версия PostgreSQL с установленным расширением PostGIS. В исходном `docker-compose.yml` указан `postgres:15`: этот контейнер сам по себе не гарантирует PostGIS. Перед сменой образа проверьте совместимость data directory, права и резервную копию.
3. Создайте отдельную учётную запись/схему или предоставьте мигратору необходимые права на `CREATE SCHEMA`/`CREATE EXTENSION`. Не публикуйте прямой порт PostgreSQL в интернет.
4. Установите зависимости и примените миграцию:

```bash
export OSTROVOK_STORAGE=postgres
export OSTROVOK_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/ostrovok_travel'
python -m intelligence.database
```

5. Затем запустите API:

```bash
export OSTROVOK_MODE=production
export OSTROVOK_API_TOKEN='your-long-random-internal-token'
uvicorn intelligence.api:app --host 127.0.0.1 --port 8090
```

6. Проверьте `/ready`; далее добавьте в Go BFF проксирование публичного read/search API и доступ по service-to-service authentication к операционным API. Пример без привязки к маршрутизатору: `examples/go-bff/intelligence.go`. **Это пример, а не уже сделанный merge в GitHub.**
7. Сопоставьте канонические hotel_id с `travel_cards.id` в `intelligence.hotel_external_ids`, только после ручной проверки идентичности. Не соединяйте объекты только по похожему названию. Пример SQL:

```sql
INSERT INTO intelligence.hotel_external_ids
  (hotel_id, provider, external_id, offer_id, identity_method, confidence, verified_at)
VALUES
  ('4b8ec4d1-edc1-59c0-ab4a-ed2c62f209a4',
   'ostrovok_go', 'real-card-id-here', 123, 'human_verified', 1.0, now());
```

Число `123` — исключительно пример, не реальный ID карточки. Идентичность с исходным каталогом пока не проверена. Если таблица `travel_cards` существует, адаптируйте внешний ключ согласно её фактическому типу и ограничениям.

## Вариант C — реальная береговая линия OSM

В среде с доступом в интернет проверьте источник:

```bash
python -m intelligence.probe
```

Затем задайте:

```bash
export OSTROVOK_LIVE_SOURCES=true
```

Из защищённой администраторской зоны запустите `POST /api/v2/intelligence/jobs/enrich`:

```json
{
  "hotel_id": "4b8ec4d1-edc1-59c0-ab4a-ed2c62f209a4",
  "feature_keys": ["coast_straight_m"],
  "mode": "live"
}
```

Заголовки: `Authorization: Bearer <OSTROVOK_API_TOKEN>`, `Idempotency-Key: live-coast-1`.

Система загрузит геометрию coastline через Overpass и вычислит минимальную **прямую геодезическую дистанцию** в PostGIS (`ST_Distance(geography, geography)`). Если геометрия не получена, будет записана ошибка шага, но не выдуманное расстояние. Если найден реальный источник, синтетические измерения перестают влиять на итоговое значение. При сохраняющихся противоречиях между реальными источниками статус будет `conflicted`.

Пешеходный маршрут, доступ к пляжу, шум, вид на закат и стройки **не подтверждаются** одной геометрией береговой линии. Для них требуются другие источники и алгоритмы. Публичные Overpass-серверы — ресурс с ограничениями; для масштабирования нужны кеш, контроль частоты и региональные снимки.

## Проверки и состояние

- `python -m pytest -q` — unit/API/regression для SQLite и адаптера.
- `cd examples/go-bff && go test ./...` — Go HTTP-клиент.
- `GET /ready` — проверка подключения к выбранному хранилищу.
- `SELECT postgis_version();` в PostgreSQL — наличие расширения.
- `SELECT COUNT(*) FROM intelligence.hotels;` — наличие каталога.
- `SELECT COUNT(*) FROM intelligence.geo_features;` — сохранённые геометрии.
- `SELECT * FROM intelligence.resolved_facts WHERE feature_key='coast_straight_m';` — полученные значения.

**В текущем артефакте не были исполнены настоящие Docker/Postgres-сессии и сетевые OSM-запросы.** Проверки соответствующих мест представлены модульными тестами и инструкциями для среды с доступом к сервисам.

Для дополнительного интеграционного теста укажите **отдельную временную базу данных**, не рабочую:

```bash
export OSTROVOK_TEST_POSTGRES_DSN='postgresql://user:password@127.0.0.1:55432/ostrovok_travel'
python -m pytest -q tests/test_postgres_integration.py
```

Этот тест создаёт схему, добавляет демонстрационный объект и сохраняет геометрию; запускать его на базе с ценными данными не следует. По умолчанию он пропускается. Если ранняя несовместимая миграция схемы `intelligence` уже применялась, сначала согласуйте изменение схемы или создайте новую тестовую базу: `CREATE TABLE IF NOT EXISTS` не конвертирует типы и колонки существующих таблиц.

## Обновление до v7

В версии v7 требуется таблица `intelligence.geo_candidates`. Перед обновлением выполните резервное копирование БД, затем `python -m intelligence.database`, который применяет `sql/001_intelligence_postgis.sql`, `sql/002_nullable_coordinates.sql` и `sql/003_geo_candidates.sql`. После обновления `GET /ready` проверяет доступность хранилища; операции координат тестируйте отдельно через `/geo/candidates`. Происхождение OSM ID и URL описано в `docs/V7_GEOAGENT.md`.


## Обновление до v8

После резервного копирования базы запустите `python -m intelligence.database`. Миграция создаёт дополнительно `intelligence.urban_records`, `intelligence.building_identities` и `intelligence.media_candidates` из `sql/004_research_v8.sql`. Компоненты используют отдельную схему `intelligence` и не изменяют таблицы оригинального Go API. Применение на настоящей PostgreSQL-базе **не проверено в среде сборки**. Перед production-обновлением выполните интеграционный прогон на копии данных.
