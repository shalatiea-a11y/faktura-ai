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
from _auth import verify_session
import _files_logic as files_logic
import _extract_logic as extract_logic


def _amount_tolerance(total):
    """Rounding slack when checking exkl_moms + moms == totalbelopp - a
    fixed few-öre tolerance flagged real invoices as broken just because of
    ordinary line-item rounding, so this scales with the invoice size
    instead (never less than 50 öre)."""
    return max(0.5, abs(total) * 0.01)


def _to_float(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _clean_rader(rader):
    if not isinstance(rader, list):
        return []
    cleaned = []
    for r in rader[:100]:
        if not isinstance(r, dict):
            continue
        cleaned.append({
            'beskrivning': (r.get('beskrivning') or '').strip()[:200],
            'antal': _to_float(r.get('antal')) or None,
            'apris': _to_float(r.get('apris')),
            'moms_procent': _to_float(r.get('moms_procent')),
            'belopp': _to_float(r.get('belopp')),
        })
    return cleaned


def _clean_moms_uppdelning(rows):
    if not isinstance(rows, list):
        return []
    cleaned = []
    for r in rows[:10]:
        if not isinstance(r, dict):
            continue
        cleaned.append({'sats': _to_float(r.get('sats')), 'belopp': _to_float(r.get('belopp'))})
    return cleaned


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def _validate(uid, fields, company_id):
    reasons = []

    required = ['leverantor', 'fakturanummer', 'fakturadatum', 'forfallodatum']
    missing = [f for f in required if not (fields.get(f) or '').strip()]
    if missing or not fields.get('totalbelopp'):
        reasons.append('Ett eller flera fält saknas')

    exkl = _to_float(fields.get('belopp_exkl_moms'))
    moms = _to_float(fields.get('moms'))
    total = _to_float(fields.get('totalbelopp'))
    if abs((exkl + moms) - total) > _amount_tolerance(total):
        reasons.append(f'Summan går inte ihop ({exkl} + {moms} ≠ {total})')

    fdatum = _parse_date(fields.get('fakturadatum'))
    fforfall = _parse_date(fields.get('forfallodatum'))
    now = datetime.now()
    if fdatum and (fdatum > now + timedelta(days=30) or fdatum < now - timedelta(days=730)):
        reasons.append('Orimligt fakturadatum')
    if fforfall and (fforfall > now + timedelta(days=730) or fforfall < now - timedelta(days=730)):
        reasons.append('Orimligt förfallodatum')

    try:
        existing = [json.loads(v) for v in hgetall(f'invoices:{uid}').values()]
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


def _list_invoices_for(uid, company_id=None):
    try:
        invoices = [json.loads(v) for v in hgetall(f'invoices:{uid}').values()]
    except StoreNotConfigured:
        return {'invoices': [], 'warning': 'store_not_configured'}
    if company_id:
        invoices = [i for i in invoices if i.get('company_id') == company_id]
    invoices.sort(key=lambda i: i.get('created_at', 0), reverse=True)
    return {'invoices': invoices}


def _add_invoice_for(uid, company_id, fields, file_id):
    if not company_id:
        return 400, {'error': 'company_required'}

    reasons = _validate(uid, fields, company_id)
    now = int(time.time())
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
        'rader': _clean_rader(fields.get('rader')),
        'moms_uppdelning': _clean_moms_uppdelning(fields.get('moms_uppdelning')),
        'status': 'needs_review' if reasons else 'ok',
        'review_reasons': reasons,
        'created_at': now,
        'history': [{'action': 'created', 'ts': now}],
    }
    try:
        hset(f'invoices:{uid}', invoice['id'], json.dumps(invoice))
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'invoice': invoice}


def list_invoices(key, company_id=None):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    return 200, _list_invoices_for(uid, company_id)


def add_invoice(key, company_id, fields, file_id):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    return _add_invoice_for(uid, company_id, fields, file_id)


def update_invoice(key, invoice_id, fields):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    try:
        raw = hget(f'invoices:{uid}', invoice_id)
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
    if 'rader' in fields:
        invoice['rader'] = _clean_rader(fields.get('rader'))
    if 'moms_uppdelning' in fields:
        invoice['moms_uppdelning'] = _clean_moms_uppdelning(fields.get('moms_uppdelning'))

    reasons = _validate(uid, invoice, invoice['company_id'])
    # Don't re-flag as a duplicate of itself - _validate scans all saved
    # invoices, and this one is already among them.
    reasons = [r for r in reasons if not r.startswith('Möjlig dubblett')]
    invoice['status'] = 'needs_review' if reasons else 'ok'
    invoice['review_reasons'] = reasons
    invoice.setdefault('history', []).append({'action': 'updated', 'ts': int(time.time())})

    hset(f'invoices:{uid}', invoice_id, json.dumps(invoice))
    return 200, {'ok': True, 'invoice': invoice}


def delete_invoice(key, invoice_id):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    try:
        hdel(f'invoices:{uid}', invoice_id)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True}


def reprocess_invoice(key, invoice_id):
    """Re-runs AI extraction against the original stored file to backfill
    'rader'/'moms_uppdelning' on invoices saved before that data was
    captured - a firm-initiated 'Läs om' action rather than an automatic
    migration, since only the byrå can judge whether it's worth redoing."""
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    try:
        raw = hget(f'invoices:{uid}', invoice_id)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    if not raw:
        return 404, {'error': 'not_found'}
    invoice = json.loads(raw)
    file_id = invoice.get('file_id')
    if not file_id:
        return 400, {'error': 'no_original_file'}

    fcode, fpayload = files_logic._get_file_for(uid, file_id)
    if fcode != 200:
        return 502, {'error': 'file_missing'}

    ecode, epayload = extract_logic._extract_invoices_internal(fpayload['media_type'], fpayload['data'])
    if ecode != 200:
        return ecode, epayload

    items = epayload.get('invoices') or []
    match = None
    if len(items) == 1:
        match = items[0]
    else:
        for item in items:
            if (item.get('fakturanummer') or '').strip() == (invoice.get('fakturanummer') or '').strip():
                match = item
                break
    if not match:
        return 404, {'error': 'no_matching_invoice_in_file'}

    invoice['rader'] = _clean_rader(match.get('rader'))
    invoice['moms_uppdelning'] = _clean_moms_uppdelning(match.get('moms_uppdelning'))
    reasons = _validate(uid, invoice, invoice['company_id'])
    reasons = [r for r in reasons if not r.startswith('Möjlig dubblett')]
    invoice['status'] = 'needs_review' if reasons else 'ok'
    invoice['review_reasons'] = reasons
    invoice.setdefault('history', []).append({'action': 'reprocessed', 'ts': int(time.time())})

    hset(f'invoices:{uid}', invoice_id, json.dumps(invoice))
    return 200, {'ok': True, 'invoice': invoice}
