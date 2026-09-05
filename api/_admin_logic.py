"""Owner-only view across every accounting firm on this deployment - never
linked from the public app, and gated by its own ADMIN_KEY env var, entirely
separate from any firm's login. Read-only: this never lets the owner see a
firm's saved documents or invoice line items, just the aggregate counts
needed to know who's using the product and who might be stuck.
"""
import hmac
import json
import os

from _store import hgetall, StoreNotConfigured
import _events_logic as events_logic

RECENT_EVENTS_LIMIT = 100


def _check_admin_key(key):
    expected = os.environ.get('ADMIN_KEY')
    return bool(expected) and hmac.compare_digest(key or '', expected)


def get_overview(key):
    if not _check_admin_key(key):
        return 403, {'error': 'forbidden'}

    try:
        users_raw = hgetall('users')
    except StoreNotConfigured:
        return 200, {'ok': True, 'warning': 'store_not_configured', 'totals': {}, 'firms': []}

    firms = []
    total_companies = 0
    total_invoices = 0
    total_needs_review = 0
    total_amount = 0.0

    for uid, raw in users_raw.items():
        try:
            user = json.loads(raw)
        except json.JSONDecodeError:
            continue

        try:
            companies = hgetall(f'companies:{uid}')
        except StoreNotConfigured:
            companies = {}
        try:
            invoices_raw = hgetall(f'invoices:{uid}')
        except StoreNotConfigured:
            invoices_raw = {}

        invoice_list = [json.loads(v) for v in invoices_raw.values()]
        needs_review = sum(1 for i in invoice_list if i.get('status') == 'needs_review')
        amount = sum((i.get('totalbelopp') or 0) for i in invoice_list)

        firms.append({
            'firm_name': user.get('firm_name', ''),
            'email': user.get('email', ''),
            'created_at': user.get('created_at', 0),
            'last_login': user.get('last_login', 0),
            'companies': len(companies),
            'invoices': len(invoice_list),
            'needs_review': needs_review,
            'total_amount': round(amount, 2),
            'gmail_connected': bool(user.get('gmail_refresh_token')),
        })
        total_companies += len(companies)
        total_invoices += len(invoice_list)
        total_needs_review += needs_review
        total_amount += amount

    firms.sort(key=lambda f: f['created_at'], reverse=True)

    recent_events = events_logic.list_events(RECENT_EVENTS_LIMIT)
    error_types = {'login_failed', 'extract_error', 'file_error', 'mail_error'}
    total_errors = sum(1 for e in recent_events if e.get('type') in error_types)

    return 200, {
        'ok': True,
        'totals': {
            'firms': len(firms),
            'companies': total_companies,
            'invoices': total_invoices,
            'needs_review': total_needs_review,
            'total_amount': round(total_amount, 2),
            'recent_errors': total_errors,
        },
        'firms': firms,
        'recent_events': recent_events,
    }
