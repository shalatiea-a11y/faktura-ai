"""Pure logic for saving/listing/deleting extracted invoices - no HTTP here."""
import json
import time
import uuid

from _store import lrange, rpush, lrem_by_value, StoreNotConfigured
from _auth import check_key


def list_invoices(key):
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    try:
        raw = lrange('invoices')
    except StoreNotConfigured:
        return 200, {'invoices': [], 'warning': 'store_not_configured'}
    invoices = [json.loads(r) for r in raw]
    invoices.sort(key=lambda i: i.get('created_at', 0), reverse=True)
    return 200, {'invoices': invoices}


def add_invoice(key, fields):
    if not check_key(key):
        return 403, {'error': 'forbidden'}

    invoice = {
        'id': str(uuid.uuid4()),
        'leverantor': (fields.get('leverantor') or '').strip()[:120],
        'fakturanummer': (fields.get('fakturanummer') or '').strip()[:60],
        'ocr': (fields.get('ocr') or '').strip()[:60],
        'fakturadatum': (fields.get('fakturadatum') or '').strip()[:10],
        'forfallodatum': (fields.get('forfallodatum') or '').strip()[:10],
        'belopp_exkl_moms': _to_float(fields.get('belopp_exkl_moms')),
        'moms': _to_float(fields.get('moms')),
        'totalbelopp': _to_float(fields.get('totalbelopp')),
        'created_at': int(time.time()),
    }
    try:
        rpush('invoices', json.dumps(invoice))
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'invoice': invoice}


def delete_invoice(key, invoice_id):
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    try:
        raw = lrange('invoices')
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    for r in raw:
        if json.loads(r).get('id') == invoice_id:
            lrem_by_value('invoices', r)
            return 200, {'ok': True}
    return 404, {'error': 'not_found'}


def _to_float(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0
