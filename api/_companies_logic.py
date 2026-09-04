"""Client-company management - one accounting firm manages several client
companies, each with its own invoice list.

Each company gets a short `code` (e.g. "KALLES") - the byrå tells whoever
forwards invoices by email to put that code in the subject line, so the
mail-checking cron job (_mail_logic.py) knows which company an incoming
invoice belongs to.

Every function is split in two: a public, key-checked entry point used by
the API (index.py) and an internal `_for(uid, ...)` version used by the
mail cron, which already knows the trusted uid for the firm it's processing
and has no user-supplied session token to check.
"""
import json
import re
import time
import uuid

from _store import hset, hgetall, StoreNotConfigured
from _auth import verify_session


def _make_code(name, existing_codes):
    """A short, memorable code someone can type from memory in an email
    subject - just the first word of the company name, not the whole thing
    truncated (which tends to produce something nobody types correctly)."""
    first_word = (name.strip().split() or [''])[0]
    base = re.sub(r'[^A-ZÅÄÖ0-9]', '', first_word.upper())[:12] or 'FORETAG'
    code = base
    n = 2
    while code in existing_codes:
        code = f'{base}{n}'
        n += 1
    return code


def _list_companies_for(uid):
    try:
        companies = [json.loads(v) for v in hgetall(f'companies:{uid}').values()]
    except StoreNotConfigured:
        return {'companies': [], 'warning': 'store_not_configured'}
    companies.sort(key=lambda c: c.get('name', ''))
    return {'companies': companies}


def _add_company_for(uid, name):
    name = (name or '').strip()[:120]
    if not name:
        return 400, {'error': 'name_required'}
    try:
        existing = [json.loads(v) for v in hgetall(f'companies:{uid}').values()]
    except StoreNotConfigured:
        existing = []
    existing_codes = {c.get('code') for c in existing}
    company = {
        'id': str(uuid.uuid4()),
        'name': name,
        'code': _make_code(name, existing_codes),
        'created_at': int(time.time()),
    }
    try:
        hset(f'companies:{uid}', company['id'], json.dumps(company))
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'company': company}


def _match_company_by_text_for(uid, text):
    """Find a company whose code appears in the given text (e.g. an email
    subject line). Case-insensitive. Returns the company dict or None."""
    try:
        companies = [json.loads(v) for v in hgetall(f'companies:{uid}').values()]
    except StoreNotConfigured:
        return None
    text_upper = (text or '').upper()
    for c in companies:
        if c.get('code') and c['code'] in text_upper:
            return c
    return None


def list_companies(key):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    return 200, _list_companies_for(uid)


def add_company(key, name):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    return _add_company_for(uid, name)
