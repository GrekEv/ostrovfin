# Сценарий 10-минутной демонстрации

Запустите сервер на `127.0.0.1:8090` согласно README.

## 1. Список реестра и гостиницы

```bash
curl -s http://127.0.0.1:8090/api/v2/intelligence/features
curl -s http://127.0.0.1:8090/api/v2/intelligence/hotels
```

Возьмите `hotel_id` из ответа и запишите:

```bash
export HOTEL_ID=<id-from-response>
```

В PowerShell использовать `$env:HOTEL_ID="..."` и подставлять `$env:HOTEL_ID` в командах.

## 2. Запросить геоисследование

```bash
curl -s -X POST http://127.0.0.1:8090/api/v2/intelligence/jobs/enrich \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: scenario-001' \
  -d "{\"hotel_id\":\"$HOTEL_ID\",\"feature_keys\":[\"coast_straight_m\",\"dogs_allowed\"],\"mode\":\"demo\"}"
```

Ответ содержит `job_id`; `GET /api/v2/intelligence/jobs/{id}` вернёт статусы. Для берега источник **синтетический**. Это только демонстрация вычисления и происхождения, не факт о реальной береговой линии.

## 3. Проверить доказательства

```bash
curl -s "http://127.0.0.1:8090/api/v2/intelligence/hotels/$HOTEL_ID/evidence?feature_key=coast_straight_m"
```

Смотрите `source_id`, `url`, `provenance`, `algorithm_version` и `input_source_ids`.

## 4. Задать вопрос отельеру

```bash
curl -s -X POST http://127.0.0.1:8090/api/v2/intelligence/hotelier/questions \
  -H 'Content-Type: application/json' \
  -d "{\"hotel_id\":\"$HOTEL_ID\",\"feature_keys\":[\"dogs_allowed\",\"pet_max_weight_kg\",\"pet_fee\"],\"question\":\"Какие условия проживания с собакой и сколько стоит?\",\"reason\":\"unknown\"}"
```

Подставьте `question_id` в следующую команду:

```bash
curl -s -X POST http://127.0.0.1:8090/api/v2/intelligence/hotelier/questions/QUESTION_ID/answer \
  -H 'Content-Type: application/json' \
  -d '{"text":"С собаками можно до 12 кг, доплата 1500 рублей за ночь."}'
```

Ответ содержит несколько `candidates`; сами по себе они не публикуются. Подтвердить нужно `POST /api/v2/intelligence/hotelier/answers/ANSWER_ID/confirm` с `candidate_ids` из ответа.

## 5. Поиск после ответа

```bash
curl -s -X POST http://127.0.0.1:8090/api/v2/intelligence/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"Отель у моря с собакой 12 кг и видом на закат"}'
```

По условиям о закате ответ должен остаться `unknown`; западные окна не доказывают вид. Метрики доступны в `GET /api/v2/intelligence/metrics`. `precision=null` означает отсутствие независимой разметки.
