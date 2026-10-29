import re
import uuid
from .store import get_profile, list_hotels


def parse_query(query):
    content = query.lower().replace('ё', 'е')
    criteria = []
    near_water = re.search(r'мор[еяюем]|пляж|побереж|берег', content)
    if near_water:
        walk = re.search(r'(\d{1,3})\s*(?:минут[а-я]*|мин\b)', content)
        direct = re.search(r'(?:не\s+дальше\s+|до\s+|не\s+более\s+)(\d{2,4})\s*(?:м(?:етр(?:ов|а)?)?\b)', content)
        if walk and re.search(r'пешк|ходьб|дойти', content):
            criteria.append({'feature_key': 'beach_walk_minutes', 'op': '<=',
                             'value': float(walk.group(1)), 'priority': 'hard'})
        elif direct:
            criteria.append({'feature_key': 'coast_straight_m', 'op': '<=',
                             'value': float(direct.group(1)), 'priority': 'soft'})
        else:
            criteria.append({'feature_key': 'coast_straight_m', 'op': '<=',
                             'value': 1000, 'priority': 'soft'})
    if re.search(r'собак|с собак|с питомц', content):
        criteria.append({'feature_key': 'dogs_allowed', 'op': '=', 'value': True, 'priority': 'hard'})
        match = re.search(r'(?:собак\w*|весом|питомц\w*)\s*(\d+(?:[,.]\d+)?)\s*кг', content)
        if match:
            criteria.append({'feature_key': 'pet_max_weight_kg', 'op': '>=',
                             'value': float(match.group(1).replace(',', '.')), 'priority': 'hard'})
    if re.search(r'кошк|кот', content):
        criteria.append({'feature_key': 'cats_allowed', 'op': '=', 'value': True, 'priority': 'hard'})
    if re.search(r'закат|запад', content):
        criteria.append({'feature_key': 'sunset_view', 'op': '=', 'value': True, 'priority': 'soft'})
    if re.search(r'тишин|тих\w*|без шума', content):
        criteria.append({'feature_key': 'noise_risk', 'op': '=', 'value': 'low', 'priority': 'soft'})
    if re.search(r'(?:без|никаких|чтоб(?:ы)? не было)\s+(?:стро(?:йк|ек)|ремонт|строител)', content):
        criteria.append({'feature_key': 'active_construction_nearby', 'op': '=',
                         'value': False, 'priority': 'hard'})
    return criteria


def evaluate(fact, criterion):
    status = fact['status']
    value = fact['value']
    if status == 'conflicted':
        return 'conflicted', 'Источники противоречат друг другу'
    if value is None or status in ('unknown', 'stale'):
        return 'unknown', 'Нет достоверных данных по характеристике'
    op, target = criterion['op'], criterion['value']
    try:
        ok = {'=': lambda: value == target, '<=': lambda: value <= target,
              '>=': lambda: value >= target}[op]()
    except (TypeError, KeyError):
        return 'unknown', 'Значение нельзя сравнить'
    if ok:
        if status == 'estimated':
            return 'partial', 'Условие совпадает только по оценочным данным'
        return 'match', 'Условие соответствует источникам'
    return 'mismatch', 'Условие не выполняется'


def search(db, registry, query, limit=10, hard_filters=None, include_demo=False):
    criteria = parse_query(query)
    for key, value in (hard_filters or {}).items():
        registry.validate(key, value)
        criteria.append({'feature_key': key, 'op': '=', 'value': value, 'priority': 'hard'})
    results = []
    for hotel in list_hotels(db):
        if hotel['coord_status'] == 'synthetic_fixture' and not include_demo:
            continue
        profile = get_profile(db, hotel['id'], registry)
        matches = []
        caveats = []
        weights = {'hard': 2, 'soft': 1}
        numerator = 0
        denominator = 0
        for criterion in criteria:
            fact = profile['facts'][criterion['feature_key']]
            status, explanation = evaluate(fact, criterion)
            weight = weights[criterion['priority']]
            numerator += weight * {'match': 1, 'partial': .5, 'mismatch': 0,
                                   'unknown': 0, 'conflicted': 0}[status]
            denominator += weight
            if criterion['priority'] == 'hard' and status != 'match':
                caveats.append('Обязательное условие не подтверждено: ' + criterion['feature_key'])
            matches.append({'feature_key': criterion['feature_key'], 'status': status,
                            'explanation': explanation, 'assertion_ids': fact['assertion_ids']})
        renovation = profile['facts'].get('renovation_notice')
        if renovation and renovation['value'] is not None:
            caveats.append('Опубликовано уведомление о ремонте; текущий статус необходимо уточнить у отеля')
        results.append({'hotel_id': hotel['id'], 'name': hotel['name'], 'address': hotel['address'],
                        'demo': hotel['coord_status'] == 'synthetic_fixture',
                        'score': round(100 * numerator / denominator) if denominator else None,
                        'matches': matches, 'caveats': caveats,
                        'eligible': bool(criteria) and not any(
                            criterion['priority'] == 'hard' and item['status'] != 'match'
                            for criterion, item in zip(criteria, matches))})
    return {'request_id': str(uuid.uuid4()), 'criteria': criteria,
            'results': sorted(results, key=lambda x: (-x['eligible'], -(x['score'] or 0), x['name']))[:limit]}
