"""Client-company management - one accounting firm (one CLIENT_KEY) manages
several client companies, each with its own invoice list."""
import json
import time
import uuid

from _store import hset, hgetall, StoreNotConfigured
from _auth import check_key


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
    company = {'id': str(uuid.uuid4()), 'name': name, 'created_at': int(time.time())}
    try:
        hset('companies', company['id'], json.dumps(company))
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'company': company}
