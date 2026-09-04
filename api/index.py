"""Single Vercel Python entrypoint for the whole API - see hallak-demo's
README for why: Vercel's Python builder wants one canonical entrypoint file
rather than auto-detecting many separate handler files.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.append(os.path.dirname(__file__))
import _extract_logic as extract_logic  # noqa: E402
import _invoices_logic as invoices_logic  # noqa: E402


PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')

STATIC_PAGES = {
    '/': 'index.html',
    '/index.html': 'index.html',
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        if path in STATIC_PAGES:
            self._send_html(STATIC_PAGES[path])
            return

        if path.startswith('/api/invoices'):
            key = (qs.get('key') or [''])[0]
            code, payload = invoices_logic.list_invoices(key)
        else:
            code, payload = 404, {'error': 'not_found'}

        self._send(code, payload)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            self._send(400, {'error': 'invalid_json'})
            return

        key = (body.get('key') or '').strip()

        if path.startswith('/api/extract'):
            media_type = body.get('media_type') or ''
            data = body.get('data') or ''
            try:
                code, payload = extract_logic.extract_invoice(key, media_type, data)
            except Exception as e:
                code, payload = 500, {'error': str(e)}
        elif path.startswith('/api/invoices'):
            action = body.get('action')
            if action == 'delete':
                code, payload = invoices_logic.delete_invoice(key, body.get('id'))
            else:
                code, payload = invoices_logic.add_invoice(key, body.get('fields') or {})
        else:
            code, payload = 404, {'error': 'not_found'}

        self._send(code, payload)

    def _send(self, code, payload):
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def _send_html(self, filename):
        try:
            with open(os.path.join(PROJECT_ROOT, filename), 'rb') as f:
                body = f.read()
        except FileNotFoundError:
            self._send(404, {'error': 'not_found'})
            return
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)
