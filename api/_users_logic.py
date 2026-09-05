"""Per-firm account settings - currently just the connected Gmail inbox for
mail-in (an OAuth refresh token from the "Anslut Gmail" button, see
_google_oauth.py - not a pasted app password).
"""
import json

from _store import hset, hget, hgetall, hdel, StoreNotConfigured, StoreRequestFailed
from _auth import verify_session


def _public_view(user):
    return {
        'email': user.get('email', ''),
        'firm_name': user.get('firm_name', ''),
        'gmail_email': user.get('gmail_email', ''),
        'gmail_connected': bool(user.get('gmail_refresh_token')),
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


def save_gmail_connection(uid, gmail_email, refresh_token):
    """Called from the OAuth callback once Google has granted access -
    no session key here, the caller already resolved+verified uid from the
    signed `state` param. Also maintains a gmail_email -> uid index, since
    an incoming push notification only tells us the mailbox address."""
    raw = hget('users', uid)
    if not raw:
        return False
    user = json.loads(raw)
    user['gmail_email'] = (gmail_email or '').strip()[:200]
    user['gmail_refresh_token'] = refresh_token
    hset('users', uid, json.dumps(user))
    if user['gmail_email']:
        hset('users_by_gmail_email', user['gmail_email'], uid)
    return True


def save_watch_state(uid, history_id, expiration):
    """Records where Gmail push notification history-sync should resume
    from, and (for our own bookkeeping) when the watch subscription expires
    - Gmail push subscriptions need renewing roughly every 7 days."""
    raw = hget('users', uid)
    if not raw:
        return
    user = json.loads(raw)
    if history_id:
        user['gmail_history_id'] = history_id
    if expiration:
        user['gmail_watch_expiration'] = expiration
    hset('users', uid, json.dumps(user))


def find_uid_by_gmail_email(gmail_email):
    try:
        return hget('users_by_gmail_email', (gmail_email or '').strip())
    except (StoreNotConfigured, StoreRequestFailed):
        return None


def disconnect_gmail(key):
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
    gmail_email = user.get('gmail_email')
    for field in ('gmail_email', 'gmail_refresh_token', 'gmail_history_id', 'gmail_watch_expiration'):
        user.pop(field, None)
    try:
        hset('users', uid, json.dumps(user))
        if gmail_email:
            hdel('users_by_gmail_email', gmail_email)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    return 200, {'ok': True, 'account': _public_view(user)}


def get_firm_name(uid):
    """Best-effort firm name lookup for event-log labels - never raises."""
    try:
        raw = hget('users', uid)
    except (StoreNotConfigured, StoreRequestFailed):
        return ''
    if not raw:
        return ''
    try:
        return json.loads(raw).get('firm_name', '')
    except json.JSONDecodeError:
        return ''


def list_gmail_connected_users():
    """For the mail cron: every firm that has connected Gmail."""
    try:
        users = [json.loads(v) for v in hgetall('users').values()]
    except StoreNotConfigured:
        return []
    return [u for u in users if u.get('gmail_refresh_token')]
