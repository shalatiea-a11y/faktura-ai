"""Local-only dev server for testing - NOT deployed. Serves static files and
dispatches /api/* to the real index.py handler, with the Redis store and the
Claude API call swapped for in-memory fakes so this runs with no external
services.
"""
import http.server
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api'))

import _store as store_mod

_fake_lists = {}

def fake_lrange(key, start=0, stop=-1):
    return _fake_lists.get(key, [])

def fake_rpush(key, value):
    _fake_lists.setdefault(key, []).append(value)

def fake_lrem_by_value(key, value):
    lst = _fake_lists.setdefault(key, [])
    if value in lst:
        lst.remove(value)

store_mod.lrange = fake_lrange
store_mod.rpush = fake_rpush
store_mod.lrem_by_value = fake_lrem_by_value

os.environ['CLIENT_KEY'] = 'demo123'

import _extract_logic

def fake_extract_invoice(key, media_type, data):
    from _auth import check_key
    if not check_key(key):
        return 403, {'error': 'forbidden'}
    return 200, {'ok': True, 'fields': {
        'leverantor': 'Testleverantör AB',
        'fakturanummer': 'F-2026-042',
        'ocr': '987654321',
        'fakturadatum': '2026-08-15',
        'forfallodatum': '2026-09-15',
        'belopp_exkl_moms': 800.0,
        'moms': 200.0,
        'totalbelopp': 1000.0,
    }}

_extract_logic.extract_invoice = fake_extract_invoice

import index as index_mod
index_mod.extract_logic.extract_invoice = fake_extract_invoice

ROOT = os.path.dirname(__file__)


class DevHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/api/'):
            self._dispatch('do_GET')
        else:
            self._serve_static()

    def do_POST(self):
        self._dispatch('do_POST')

    def _dispatch(self, method_name):
        h = index_mod.handler.__new__(index_mod.handler)
        h.path = self.path
        h.headers = self.headers
        h.rfile = self.rfile
        h.wfile = self.wfile
        h.client_address = self.client_address
        h._headers_buffer = []
        h.requestline = self.requestline
        h.request_version = self.request_version
        h.log_message = lambda *a, **k: None
        getattr(h, method_name)()

    def _serve_static(self):
        path = self.path.split('?')[0]
        if path == '/':
            path = '/index.html'
        fpath = os.path.join(ROOT, path.lstrip('/'))
        if not os.path.isfile(fpath):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        ctype = 'text/html' if fpath.endswith('.html') else 'application/octet-stream'
        self.send_header('Content-type', ctype)
        self.end_headers()
        with open(fpath, 'rb') as f:
            self.wfile.write(f.read())

    def log_message(self, *a, **k):
        pass


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8130
    server = http.server.HTTPServer(('127.0.0.1', port), DevHandler)
    print(f'Serving on http://127.0.0.1:{port}')
    server.serve_forever()
