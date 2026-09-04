"""Pure logic for saving/listing/updating/deleting extracted invoices.

Every newly extracted invoice is auto-saved immediately (per the product
decision: batches of up to 20 files should require zero clicks per invoice),
but each one is validated first and, if anything looks off, saved with
status='needs_review' plus a list of human-readable reasons so the
accountant can see exactly what to check - rather than silently trusting
the AI or silently blocking the save.
"""
import json
import time
import uuid
from datetime import datetime, timedelta

from _store import hset, hget, hgetall, hdel, StoreNotConfigured
from _auth import check_key

AMOUNT_TOLERANCE = 0.05  # kr - float rounding slack when checking exkl_moms + moms == totalbelopp


def _to_float(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def _validate(fields, company_id):
    reasons = []

    required = ['leverantor', 'fakturanummer', 'fakturadatum', 'forfallodatum']
    missing = [f for f in required if not (fields.get(f) or '').strip()]
    if missing or not fields.get('totalbelopp'):
        reasons.append('Ett eller flera fält saknas')

    exkl = _to_float(fields.get('belopp_exkl_moms'))
    moms = _to_float(fields.get('moms'))
    total = _to_float(fields.get('totalbelopp'))
    if abs((exkl + moms) - total) > AMOUNT_TOLERANCE:
        reasons.append(f'Summan går inte ihop ({exkl} + {moms} ≠ {total})')

    fdatum = _parse_date(fields.get('fakturadatum'))
    fforfall = _parse_date(fields.get('forfallodatum'))
    now = datetime.now()
    if fdatum and (fdatum > now + timedelta(days=30) or fdatum < now - timedelta(days=730)):
        reasons.append('Orimligt fakturadatum')
    if fforfall and (fforfall > now + timedelta(days=730) or fforfall < now - timedelta(days=730)):
        reasons.append('Orimligt förfallodatum')

    try:
        existing = [json.loads(v) for v in hgetall('invoices').values()]
    except StoreNotConfigured:
        existing = []
    dup = any(
        inv.get('company_id') == company_id
        and inv.get('leverantor', '').strip().lower() == (fields.get('leverantor') or '').strip().lower()
        and inv.get('fakturanummer', '').strip() == (fields.get('fakturanummer') or '').strip()
        and inv.get('fakturanummer', '').strip()
        for inv in existing
    )
    if dup:
        reasons.append('Möjlig dubblett - samma leverantör + fakturanummer finns redan')

    return reasons


def list_invoices(key, company_id=None):
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    try:
        invoices = [json.loads(v) for v in hgetall('invoices').values()]
    except StoreNotConfigured:
        return 200, {'invoices': [], 'warning': 'store_not_configured'}
    if company_id:
        invoices = [i for i in invoices if i.get('company_id') == company_id]
    invoices.sort(key=lambda i: i.get('created_at', 0), reverse=True)
    return 200, {'invoices': invoices}


def add_invoice(key, company_id, fields, file_id):
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    if not company_id:
        return 400, {'error': 'company_required'}

    reasons = _validate(fields, company_id)
    invoice = {
        'id': str(uuid.uuid4()),
        'company_id': company_id,
        'file_id': file_id or '',
        'leverantor': (fields.get('leverantor') or '').strip()[:120],
        'fakturanummer': (fields.get('fakturanummer') or '').strip()[:60],
        'ocr': (fields.get('ocr') or '').strip()[:60],
        'fakturadatum': (fields.get('fakturadatum') or '').strip()[:10],
        'forfallodatum': (fields.get('forfallodatum') or '').strip()[:10],
        'belopp_exkl_moms': _to_float(fields.get('belopp_exkl_moms')),
        'moms': _to_float(fields.get('moms')),
        'totalbelopp': _to_float(fields.get('totalbelopp')),
        'status': 'needs_review' if reasons else 'ok',
        'review_reasons': reasons,
        'created_at': int(time.time()),
    }
    try:
        hset('invoices', invoice['id'], json.dumps(invoice))
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'invoice': invoice}


def update_invoice(key, invoice_id, fields):
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    try:
        raw = hget('invoices', invoice_id)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    if not raw:
        return 404, {'error': 'not_found'}

    invoice = json.loads(raw)
    for f in ['leverantor', 'fakturanummer', 'ocr', 'fakturadatum', 'forfallodatum']:
        if f in fields:
            invoice[f] = (fields.get(f) or '').strip()[:120]
    for f in ['belopp_exkl_moms', 'moms', 'totalbelopp']:
        if f in fields:
            invoice[f] = _to_float(fields.get(f))

    reasons = _validate(invoice, invoice['company_id'])
    # Don't re-flag as a duplicate of itself - _validate scans all saved
    # invoices, and this one is already among them.
    reasons = [r for r in reasons if not r.startswith('Möjlig dubblett')]
    invoice['status'] = 'needs_review' if reasons else 'ok'
    invoice['review_reasons'] = reasons

    hset('invoices', invoice_id, json.dumps(invoice))
    return 200, {'ok': True, 'invoice': invoice}


def delete_invoice(key, invoice_id):
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    try:
        hdel('invoices', invoice_id)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True}
