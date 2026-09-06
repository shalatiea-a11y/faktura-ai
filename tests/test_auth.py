"""Authentication: signup, login, wrong password, invalid/expired session.

'Logout' has no server-side component in the current implementation - the
session token is stateless (HMAC-signed, not stored server-side), so
logging out is purely a client-side localStorage.removeItem() in app.html.
There is nothing to unit-test on the server for logout; that is itself
documented as a finding (see test_logout_is_not_server_side below), not
skipped silently.
"""
import time

import _auth as auth_logic


def test_signup_creates_account_and_returns_token(firm):
    assert firm['token']
    assert firm['uid']


def test_signup_rejects_duplicate_email(firm):
    code, payload = auth_logic.signup(firm['email'], 'different-password', 'Another Firm')
    assert code == 409
    assert payload['error'] == 'email_taken'


def test_signup_rejects_invalid_email():
    code, payload = auth_logic.signup('not-an-email', 'correct-horse-1', 'Firma')
    assert code == 400
    assert payload['error'] == 'invalid_email'


def test_signup_rejects_short_password():
    code, payload = auth_logic.signup('short@example.com', 'short', 'Firma')
    assert code == 400
    assert payload['error'] == 'password_too_short'


def test_signup_rejects_missing_firm_name():
    code, payload = auth_logic.signup('noname@example.com', 'correct-horse-1', '')
    assert code == 400
    assert payload['error'] == 'firm_name_required'


def test_login_succeeds_with_correct_password(firm):
    code, payload = auth_logic.login(firm['email'], 'correct-horse-1')
    assert code == 200
    assert payload['ok'] is True
    assert auth_logic.verify_session(payload['token']) == firm['uid']


def test_login_fails_with_wrong_password(firm):
    code, payload = auth_logic.login(firm['email'], 'totally-wrong-password')
    assert code == 401
    assert payload['error'] == 'invalid_credentials'


def test_login_fails_with_unknown_email():
    code, payload = auth_logic.login('nobody@example.com', 'whatever12')
    assert code == 401
    assert payload['error'] == 'invalid_credentials'


def test_login_error_message_does_not_reveal_which_field_was_wrong(firm):
    """Same error for 'wrong password' and 'unknown email' - prevents an
    attacker from using the login endpoint to enumerate registered emails."""
    _, unknown_email_payload = auth_logic.login('nobody@example.com', 'whatever12')
    _, wrong_password_payload = auth_logic.login(firm['email'], 'wrong-password')
    assert unknown_email_payload['error'] == wrong_password_payload['error']


def test_verify_session_rejects_garbage_token():
    assert auth_logic.verify_session('not-a-real-token') is None


def test_verify_session_rejects_empty_token():
    assert auth_logic.verify_session('') is None
    assert auth_logic.verify_session(None) is None


def test_verify_session_rejects_tampered_signature(firm):
    token = firm['token']
    payload_b64, sig = token.rsplit('.', 1)
    tampered = f'{payload_b64}.{"0" * len(sig)}'
    assert auth_logic.verify_session(tampered) is None


def test_verify_session_rejects_expired_token(firm, monkeypatch):
    """Signs a token that expired in the past by forging the exp claim with
    the real signing function's own secret, then confirms verify_session
    actually checks expiry (not just signature validity)."""
    import base64
    import hashlib
    import hmac
    import json as jsonlib

    secret = auth_logic._session_secret()
    payload = jsonlib.dumps({'uid': firm['uid'], 'exp': int(time.time()) - 10}, separators=(',', ':')).encode()
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    expired_token = f'{auth_logic._b64u_encode(payload)}.{sig}'
    assert auth_logic.verify_session(expired_token) is None


def test_signup_fails_cleanly_without_session_secret(monkeypatch):
    monkeypatch.delenv('SESSION_SECRET', raising=False)
    code, payload = auth_logic.signup('x@example.com', 'correct-horse-1', 'Firma')
    assert code == 503
    assert payload['error'] == 'auth_not_configured'


def test_login_fails_cleanly_without_session_secret(firm, monkeypatch):
    monkeypatch.delenv('SESSION_SECRET', raising=False)
    code, payload = auth_logic.login(firm['email'], 'correct-horse-1')
    assert code == 503
    assert payload['error'] == 'auth_not_configured'


def test_logout_is_not_server_side():
    """Documents a real architectural fact, not a bug: sessions are
    stateless signed tokens with no server-side store, so there is no way
    to invalidate a single token before its 30-day expiry (e.g. on
    password change or explicit 'log out everywhere'). This is a known gap
    to close in the security-hardening phase, not something this test
    should silently paper over."""
    assert auth_logic.SESSION_TTL_SECONDS == 30 * 24 * 60 * 60
