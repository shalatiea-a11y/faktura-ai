"""Per-firm account settings - currently just the mail-in inbox credentials
(previously a single global MAIL_ADDRESS/MAIL_APP_PASSWORD env var pair
shared by everyone; now each firm can plug in its own dedicated inbox from
the Inställningar page).
"""
import json

from _store import hset, hget, hgetall, StoreNotConfigured
from _auth import verify_session


def _public_view(user):
    return {
        'email': user.get('email', ''),
        'firm_name': user.get('firm_name', ''),
        'mail_address': user.get('mail_address', ''),
        'mail_configured': bool(user.get('mail_address') and user.get('mail_app_password')),
    }


def get_account(key):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    try:
        raw = hget('users', uid)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    if not raw:
        return 404, {'error': 'not_found'}
    return 200, {'ok': True, 'account': _public_view(json.loads(raw))}


def update_mail_settings(key, mail_address, mail_app_password):
    uid = verify_session(key)
    if not uid:
        return 403, {'error': 'forbidden'}
    try:
        raw = hget('users', uid)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    if not raw:
        return 404, {'error': 'not_found'}

    user = json.loads(raw)
    user['mail_address'] = (mail_address or '').strip()[:200]
    user['mail_app_password'] = (mail_app_password or '').strip()[:200]
    try:
        hset('users', uid, json.dumps(user))
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'account': _public_view(user)}


def list_mail_enabled_users():
    """For the mail cron: every firm that has plugged in an inbox."""
    try:
        users = [json.loads(v) for v in hgetall('users').values()]
    except StoreNotConfigured:
        return []
    return [u for u in users if u.get('mail_address') and u.get('mail_app_password')]
