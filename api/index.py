"""Single Vercel Python entrypoint for the whole API - see hallak-demo's
README for why: Vercel's Python builder wants one canonical entrypoint file
rather than auto-detecting many separate handler files, and (per the lesson
learned there) appears to swallow ALL paths into this one function - so this
also serves index.html directly rather than relying on Vercel's static
file server.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.append(os.path.dirname(__file__))
import _extract_logic as extract_logic  # noqa: E402
import _invoices_logic as invoices_logic  # noqa: E402
import _companies_logic as companies_logic  # noqa: E402
import _files_logic as files_logic  # noqa: E402
import _mail_logic as mail_logic  # noqa: E402

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')

STATIC_PAGES = {
    '/': 'index.html',
    '/index.html': 'index.html',
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        key = (qs.get('key') or [''])[0]

        if path in STATIC_PAGES:
            self._send_html(STATIC_PAGES[path])
            return

        if path.startswith('/api/cron/check-mail'):
            expected = os.environ.get('CRON_SECRET')
            auth = self.headers.get('Authorization', '')
            if not expected or auth != f'Bearer {expected}':
                self._send(401, {'error': 'unauthorized'})
                return
            code, payload = mail_logic.check_inbox()
            self._send(code, payload)
            return

        if path.startswith('/api/companies'):
            code, payload = companies_logic.list_companies(key)
        elif path.startswith('/api/invoices'):
            company_id = (qs.get('company_id') or [None])[0]
            code, payload = invoices_logic.list_invoices(key, company_id)
        elif path.startswith('/api/files'):
            file_id = (qs.get('id') or [''])[0]
            code, payload = files_logic.get_file(key, file_id)
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
                code, payload = extract_logic.extract_invoices(key, media_type, data)
            except Exception as e:
                code, payload = 500, {'error': str(e)}

        elif path.startswith('/api/files'):
            media_type = body.get('media_type') or ''
            data = body.get('data') or ''
            code, payload = files_logic.save_file(key, media_type, data)

        elif path.startswith('/api/companies'):
            code, payload = companies_logic.add_company(key, body.get('name'))

        elif path.startswith('/api/invoices'):
            action = body.get('action')
            if action == 'update':
                code, payload = invoices_logic.update_invoice(key, body.get('id'), body.get('fields') or {})
            elif action == 'delete':
                code, payload = invoices_logic.delete_invoice(key, body.get('id'))
            else:
                code, payload = invoices_logic.add_invoice(
                    key, body.get('company_id'), body.get('fields') or {}, body.get('file_id'),
                )

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
