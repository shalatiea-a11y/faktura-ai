"""Client-company management - one accounting firm (one CLIENT_KEY) manages
several client companies, each with its own invoice list.

Each company gets a short `code` (e.g. "KALLES") - the byrå tells whoever
forwards invoices by email to put that code in the subject line, so the
mail-checking cron job (_mail_logic.py) knows which company an incoming
invoice belongs to.
"""
import json
import re
import time
import uuid

from _store import hset, hgetall, StoreNotConfigured
from _auth import check_key

UNCATEGORIZED_ID = 'uncategorized'


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


def list_companies(key):
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    try:
        companies = [json.loads(v) for v in hgetall('companies').values()]
    except StoreNotConfigured:
        return 200, {'companies': [], 'warning': 'store_not_configured'}
    companies.sort(key=lambda c: c.get('name', ''))
    return 200, {'companies': companies}


def add_company(key, name):
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    name = (name or '').strip()[:120]
    if not name:
        return 400, {'error': 'name_required'}
    try:
        existing = [json.loads(v) for v in hgetall('companies').values()]
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
        hset('companies', company['id'], json.dumps(company))
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'company': company}


def match_company_by_text(text):
    """Find a company whose code appears in the given text (e.g. an email
    subject line). Case-insensitive. Returns the company dict or None."""
    try:
        companies = [json.loads(v) for v in hgetall('companies').values()]
    except StoreNotConfigured:
        return None
    text_upper = (text or '').upper()
    for c in companies:
        if c.get('code') and c['code'] in text_upper:
            return c
    return None
