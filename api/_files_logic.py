"""Stores the original uploaded invoice file (image/PDF) alongside the
extracted data - Swedish bookkeeping law (bokföringslagen) requires the
original underlying document to be kept, not just the numbers read off it.
"""
import json
import uuid

from _store import hset, hget, StoreNotConfigured
from _auth import verify_session
from _media import resolve_media_type
import _events_logic as events_logic
import _users_logic as users_logic

MAX_FILE_BYTES = 8 * 1024 * 1024  # ~8MB before base64 - generous for a scanned invoice


def _save_file_for(uid, media_type, base64_data, filename=None):
    if not base64_data:
        return 400, {'error': 'missing_fields'}
    if len(base64_data) > MAX_FILE_BYTES * 1.4:  # base64 overhead
        return 413, {'error': 'file_too_large'}

    media_type = resolve_media_type(media_type, filename) or media_type or 'application/octet-stream'
    file_id = str(uuid.uuid4())
    try:
        hset(f'files:{uid}', file_id, json.dumps({'media_type': media_type, 'data': base64_data}))
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'file_id': file_id}


def _get_file_for(uid, file_id):
    try:
        raw = hget(f'files:{uid}', file_id)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    if not raw:
        return 404, {'error': 'not_found'}
    return 200, json.loads(raw)


def save_file(key, media_type, base64_data, filename=None):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    code, payload = _save_file_for(uid, media_type, base64_data, filename)
    if code != 200:
        firm_name = users_logic.get_firm_name(uid)
        events_logic.log_event('file_error', uid, firm_name, detail=str(payload.get('error', ''))[:200])
    return code, payload


def get_file(key, file_id):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    return _get_file_for(uid, file_id)
