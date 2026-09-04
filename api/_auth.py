"""Real accounts - one account per accounting firm (byrå), replacing the old
single shared CLIENT_KEY.

Password hashing: stdlib `hashlib.pbkdf2_hmac`, no bcrypt dependency needed
(same "stdlib only, no pip deps on Vercel" rule as the rest of this app).

Sessions: a signed, stateless token - `<base64url(json payload)>.<hmac
signature>` - verified with SESSION_SECRET (a server-only secret set as a
Vercel env var). No server-side session storage needed; the token itself
carries the firm's id (`uid`) and an expiry. This is exactly what the old
`key` query/body parameter already carried around everywhere (a string that
proves who's asking) - only what's *inside* that string changed, so every
other module just swaps `check_key(key)` (a bool) for `verify_session(key)`
(the uid, or None).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid

from _store import hset, hget, StoreNotConfigured

SESSION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
PBKDF2_ITERATIONS = 200_000
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _b64u_encode(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).decode().rstrip('=')


def _b64u_decode(s):
    padding = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _session_secret():
    return (os.environ.get('SESSION_SECRET') or '').encode()


def _sign_session(uid):
    payload = json.dumps({'uid': uid, 'exp': int(time.time()) + SESSION_TTL_SECONDS}, separators=(',', ':')).encode()
    secret = _session_secret()
    if not secret:
        return None
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return f'{_b64u_encode(payload)}.{sig}'


def verify_session(token):
    """Returns the firm's uid if the token is valid and not expired, else None."""
    secret = _session_secret()
    if not secret or not token or '.' not in token:
        return None
    payload_b64, sig = token.rsplit('.', 1)
    try:
        payload_raw = _b64u_decode(payload_b64)
    except Exception:
        return None
    expected_sig = hmac.new(secret, payload_raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        payload = json.loads(payload_raw)
    except Exception:
        return None
    if payload.get('exp', 0) < time.time():
        return None
    uid = payload.get('uid')
    return uid if isinstance(uid, str) and uid else None


def _hash_password(password, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def _verify_password(password, salt_hex, hash_hex):
    salt = bytes.fromhex(salt_hex)
    _, digest_hex = _hash_password(password, salt)
    return hmac.compare_digest(digest_hex, hash_hex)


def signup(email, password, firm_name):
    if not os.environ.get('SESSION_SECRET'):
        return 503, {'error': 'auth_not_configured'}

    email = (email or '').strip().lower()
    firm_name = (firm_name or '').strip()[:120]
    if not EMAIL_RE.match(email):
        return 400, {'error': 'invalid_email'}
    if not firm_name:
        return 400, {'error': 'firm_name_required'}
    if len(password or '') < 8:
        return 400, {'error': 'password_too_short'}

    try:
        if hget('users_by_email', email):
            return 409, {'error': 'email_taken'}
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}

    uid = str(uuid.uuid4())
    salt_hex, hash_hex = _hash_password(password)
    user = {
        'id': uid,
        'email': email,
        'firm_name': firm_name,
        'salt': salt_hex,
        'password_hash': hash_hex,
        'created_at': int(time.time()),
    }
    try:
        hset('users', uid, json.dumps(user))
        hset('users_by_email', email, uid)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}

    token = _sign_session(uid)
    if not token:
        return 503, {'error': 'auth_not_configured'}
    return 200, {'ok': True, 'token': token, 'firm_name': firm_name, 'email': email}


def login(email, password):
    if not os.environ.get('SESSION_SECRET'):
        return 503, {'error': 'auth_not_configured'}

    email = (email or '').strip().lower()
    try:
        uid = hget('users_by_email', email)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    if not uid:
        return 401, {'error': 'invalid_credentials'}

    try:
        raw = hget('users', uid)
    except StoreNotConfigured:
        return 503, {'error': 'store_not_configured'}
    if not raw:
        return 401, {'error': 'invalid_credentials'}

    user = json.loads(raw)
    if not _verify_password(password or '', user['salt'], user['password_hash']):
        return 401, {'error': 'invalid_credentials'}

    token = _sign_session(uid)
    if not token:
        return 503, {'error': 'auth_not_configured'}
    return 200, {'ok': True, 'token': token, 'firm_name': user.get('firm_name', ''), 'email': user.get('email', '')}
