import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl


class TelegramAuthError(ValueError):
    pass


def verify_init_data(init_data, bot_token, now=None, max_age=86400):
    if not init_data or not bot_token:
        raise TelegramAuthError('Telegram authentication is not configured')
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    fields = dict(pairs)
    if len(fields) != len(pairs) or not fields.get('hash'):
        raise TelegramAuthError('Malformed Telegram data')
    supplied_hash = fields.pop('hash')
    if len(supplied_hash) != 64:
        raise TelegramAuthError('Invalid Telegram signature')
    payload = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_hash, expected):
        raise TelegramAuthError('Invalid Telegram signature')
    try:
        auth_date = int(fields['auth_date'])
        user = json.loads(fields['user'])
        user_id = int(user['id'])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise TelegramAuthError('Missing Telegram user or auth_date') from error
    current = int(now if now is not None else time.time())
    if auth_date > current + 60 or current - auth_date > max_age:
        raise TelegramAuthError('Expired Telegram authentication')
    if user_id <= 0:
        raise TelegramAuthError('Invalid Telegram user')
    return user


def hotelier_assignment(user_id):
    try:
        bindings = json.loads(os.getenv('OSTROVOK_HOTELIER_BINDINGS_JSON', '{}'))
    except json.JSONDecodeError as error:
        raise RuntimeError('Invalid OSTROVOK_HOTELIER_BINDINGS_JSON') from error
    return bindings.get(str(user_id))


def verified_hotelier(init_data):
    user = verify_init_data(init_data, os.getenv('TELEGRAM_BOT_TOKEN', ''))
    return user, hotelier_assignment(user['id'])
