import re
import uuid
from .connectors import Source, now_utc
from .store import audit, encode, iso_now, put_assertion, put_source, session
from .registry import Registry


def extract(text):
    content = text.lower().replace('ё', 'е').strip()
    candidates = []
    dog_negative = re.search(r'соба(?:к|ками)[^.!?]{0,25}(?:нельзя|запрещен|не принимаем)|(?:без|никаких) животных|(?:с )?животными нельзя', content)
    dog_positive = re.search(r'(?:собак|с собаками|собачек)[^.!?]{0,35}(?:можно|разрешен|принимаем)|(?:можно|принимаем|разрешен)[^.!?]{0,35}собак', content)
    if dog_negative:
        candidates.append(('dogs_allowed', False, dog_negative.group()))
    elif dog_positive:
        candidates.append(('dogs_allowed', True, dog_positive.group()))
    cat_negative = re.search(r'(?:с кошками|кошек)[^.!?]{0,25}(?:нельзя|запрещен)|(?:с кошками|кошек) не принимаем', content)
    cat_positive = re.search(r'(?:кошек|с кошками)[^.!?]{0,30}(?:можно|принимаем|разрешен)|(?:можно|принимаем)[^.!?]{0,30}кошек', content)
    if cat_negative:
        candidates.append(('cats_allowed', False, cat_negative.group()))
    elif cat_positive:
        candidates.append(('cats_allowed', True, cat_positive.group()))
    maximum = re.search(r'(?:до|не более|максимум)\s*(\d+(?:[,.]\d+)?)\s*кг', content)
    if maximum:
        candidates.append(('pet_max_weight_kg', float(maximum.group(1).replace(',', '.')), maximum.group()))
    fee = re.search(r'(\d[\d\s]*)\s*(?:руб(?:лей|ля)?|₽)\s*(?:за\s*(ночь|сутки|заезд))?', content)
    if fee and fee.group(2):
        period = 'night' if fee.group(2) in ('ночь', 'сутки') else 'stay'
        candidates.append(('pet_fee', {'amount': int(fee.group(1).replace(' ', '')), 'currency': 'RUB', 'per': period}, fee.group()))
    return candidates


def create_question(db, hotel_id, keys, question, reason, registry):
    for key in keys:
        registry.get(key)
    if not db.execute('SELECT 1 FROM hotels WHERE id=?', (hotel_id,)).fetchone():
        raise ValueError('Unknown hotel')
    qid = str(uuid.uuid4())
    db.execute('INSERT INTO questions VALUES (?,?,?,?,?,?,?)',
               (qid, hotel_id, encode(keys), question, reason, 'open', iso_now()))
    return qid


def answer_question(db, question_id, text, registry):
    question = db.execute('SELECT * FROM questions WHERE id=?', (question_id,)).fetchone()
    if not question:
        raise LookupError('Question not found')
    if question['status'] != 'open':
        raise ValueError('Already answered')
    allowed = set(__import__('json').loads(question['feature_keys']))
    answer_id = str(uuid.uuid4())
    db.execute('INSERT INTO answers VALUES (?,?,?,?,?)',
               (answer_id, question_id, text, iso_now(), 'needs_confirmation'))
    data = []
    for key, value, excerpt in extract(text):
        if key not in allowed:
            continue
        registry.validate(key, value)
        cid = str(uuid.uuid4())
        db.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?)',
                   (cid, answer_id, key, encode(value), excerpt, 'proposed'))
        data.append({'id': cid, 'feature_key': key, 'value': value, 'raw_excerpt': excerpt, 'status': 'proposed'})
    db.execute('UPDATE questions SET status=? WHERE id=?', ('answered', question_id))
    return answer_id, data


def confirm_answer(db, answer_id, candidate_ids, registry):
    answer = db.execute('SELECT * FROM answers WHERE id=?', (answer_id,)).fetchone()
    if not answer:
        raise LookupError('Answer not found')
    if answer['status'] != 'needs_confirmation':
        raise ValueError('Answer already processed')
    question = db.execute('SELECT * FROM questions WHERE id=?', (answer['question_id'],)).fetchone()
    candidates = db.execute('SELECT * FROM candidates WHERE answer_id=?', (answer_id,)).fetchall()
    valid_ids = {item['id'] for item in candidates}
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)) or not set(candidate_ids) <= valid_ids:
        raise ValueError('Unknown or duplicate candidate')
    source = Source('hotelier', answer_id, 'urn:ostrovok:hotelier:' + answer_id,
                    {'hotel_id': question['hotel_id'], 'answer_id': answer_id, 'raw_text': answer['text']},
                    now_utc(), 'hotelier_confirmed', 'private')
    source_id = put_source(db, source)
    published = []
    import json
    for candidate in candidates:
        if candidate['id'] not in candidate_ids:
            continue
        key, value = candidate['feature_key'], json.loads(candidate['value_json'])
        registry.validate(key, value)
        assertion_id = put_assertion(db, registry, question['hotel_id'], key, value, source_id,
                                     'hotelier_reported', 0.85)
        db.execute('UPDATE candidates SET status=? WHERE id=?', ('confirmed', candidate['id']))
        published.append({'feature_key': key, 'value': value, 'assertion_id': assertion_id})
    db.execute('UPDATE answers SET status=? WHERE id=?', ('confirmed', answer_id))
    audit(db, 'hotelier.answer_confirmed', question['hotel_id'], {'answer_id': answer_id, 'published': published})
    return published
