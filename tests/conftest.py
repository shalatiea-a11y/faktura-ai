"""Pytest fixtures for faktura-ai.

Fakes Redis, Claude, and the Gmail API so the whole suite runs with zero
external services, zero network calls, and zero API cost.

Import-order trick: every api/_*.py module does `from _store import hset,
hget, ...` - a name import binds the function object at import time, so
patching `_store.hset` *after* those modules already imported it would not
reach them. `_local_test_server.py` solved this by patching the store
module's attributes before importing anything that depends on it; this
file does the same thing, and since pytest always imports conftest.py
before collecting/importing test modules, every test module's own
`import _auth` (etc.) is guaranteed to see the already-patched fakes.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

os.environ.setdefault('SESSION_SECRET', 'test-session-secret-not-for-prod')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-not-for-prod')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test-anthropic-key-not-for-prod')

import _store as store_mod  # noqa: E402

_fake_hashes = {}
_fake_lists = {}


def _reset_fake_store():
    _fake_hashes.clear()
    _fake_lists.clear()


def _fake_hset(key, field, value):
    _fake_hashes.setdefault(key, {})[field] = value
    return 1


def _fake_hget(key, field):
    return _fake_hashes.get(key, {}).get(field)


def _fake_hgetall(key):
    return dict(_fake_hashes.get(key, {}))


def _fake_hdel(key, field):
    _fake_hashes.get(key, {}).pop(field, None)
    return 1


def _fake_rpush(key, value):
    _fake_lists.setdefault(key, []).append(value)
    return len(_fake_lists[key])


def _norm_range(lst, start, stop):
    n = len(lst)
    norm = lambda i: max(0, n + i) if i < 0 else min(i, n)
    return norm(start), norm(stop) + 1


def _fake_lrange(key, start, stop):
    lst = _fake_lists.get(key, [])
    s, e = _norm_range(lst, start, stop)
    return lst[s:e]


def _fake_ltrim(key, start, stop):
    lst = _fake_lists.get(key, [])
    s, e = _norm_range(lst, start, stop)
    _fake_lists[key] = lst[s:e]


store_mod.hset = _fake_hset
store_mod.hget = _fake_hget
store_mod.hgetall = _fake_hgetall
store_mod.hdel = _fake_hdel
store_mod.rpush = _fake_rpush
store_mod.lrange = _fake_lrange
store_mod.ltrim = _fake_ltrim

import pytest  # noqa: E402

import _auth as auth_logic  # noqa: E402
import _companies_logic as companies_logic  # noqa: E402
import _invoices_logic as invoices_logic  # noqa: E402
import _files_logic as files_logic  # noqa: E402
import _extract_logic as extract_logic  # noqa: E402
import _users_logic as users_logic  # noqa: E402
import _admin_logic as admin_logic  # noqa: E402
import _events_logic as events_logic  # noqa: E402
import _mail_logic as mail_logic  # noqa: E402
import _google_oauth as google_oauth  # noqa: E402


@pytest.fixture(autouse=True)
def clean_store():
    """Every test starts with an empty fake database - state must never
    leak between tests, since that would let a test pass only because an
    earlier test happened to run first."""
    _reset_fake_store()
    yield
    _reset_fake_store()


@pytest.fixture
def firm(monkeypatch=None):
    """Creates one real firm via the real signup() code path (not a
    shortcut) and returns its identity - so every test that needs 'a
    logged-in firm' incidentally keeps re-verifying that signup works."""
    code, payload = auth_logic.signup('firm1@example.com', 'correct-horse-1', 'Firma Ett AB')
    assert code == 200, payload
    token = payload['token']
    return {'token': token, 'uid': auth_logic.verify_session(token),
            'email': 'firm1@example.com', 'firm_name': 'Firma Ett AB'}


@pytest.fixture
def other_firm():
    """A second, independent firm - for tenant-isolation tests."""
    code, payload = auth_logic.signup('firm2@example.com', 'correct-horse-2', 'Firma Två AB')
    assert code == 200, payload
    token = payload['token']
    return {'token': token, 'uid': auth_logic.verify_session(token),
            'email': 'firm2@example.com', 'firm_name': 'Firma Två AB'}


@pytest.fixture
def sample_pdf_b64():
    return base64.b64encode(b'%PDF-1.4 fake invoice content').decode()


class _FakeHTTPResponse:
    """Minimal stand-in for what urllib.request.urlopen(...) returns as a
    context manager, supporting only what the app's code actually calls."""
    def __init__(self, body_bytes):
        self._body = body_bytes

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_claude(monkeypatch):
    """Controls what Claude 'returns' by patching urllib.request.urlopen -
    this exercises the REAL _extract_invoices_internal code (media-type
    check, JSON-array regex parsing, error handling) end to end, rather
    than replacing the whole function like _local_test_server.py does.
    That whole-function replacement is a real blind spot (confirmed during
    Phase 1 verification: it let an unsupported-media-type request through
    without ever hitting the real validation code), so this fixture is
    deliberately lower-level."""
    state = {'text': None, 'http_error': None, 'generic_error': None}

    def fake_urlopen(req, timeout=60):
        if state['generic_error']:
            raise state['generic_error']
        if state['http_error']:
            raise state['http_error']
        body = json.dumps({'content': [{'text': state['text']}]}).encode()
        return _FakeHTTPResponse(body)

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)

    class Controller:
        @staticmethod
        def set_response(invoices):
            state['text'] = json.dumps(invoices)

        @staticmethod
        def set_raw_text(text):
            state['text'] = text

        @staticmethod
        def set_http_error(code=502, body=b'boom'):
            err = urllib.error.HTTPError('https://api.anthropic.com/v1/messages', code, 'error', {}, None)
            err.read = lambda: body
            state['http_error'] = err

        @staticmethod
        def set_generic_error(exc):
            state['generic_error'] = exc

    return Controller()
