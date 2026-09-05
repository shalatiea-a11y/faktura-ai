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
import _auth as auth_logic  # noqa: E402
import _users_logic as users_logic  # noqa: E402
import _admin_logic as admin_logic  # noqa: E402
import _events_logic as events_logic  # noqa: E402
import _google_oauth as google_oauth  # noqa: E402
from _store import StoreNotConfigured, StoreRequestFailed  # noqa: E402

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')

STATIC_PAGES = {
    '/': 'index.html',
    '/index.html': 'index.html',
    '/login.html': 'login.html',
    '/signup.html': 'signup.html',
    '/app.html': 'app.html',
    '/admin.html': 'admin.html',
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self._route_get()
        except (StoreNotConfigured, StoreRequestFailed) as e:
            self._send(502, {'error': 'store_error', 'detail': str(e)[:300]})
        except Exception as e:
            self._send(500, {'error': 'server_error', 'detail': str(e)[:300]})

    def do_POST(self):
        try:
            self._route_post()
        except (StoreNotConfigured, StoreRequestFailed) as e:
            self._send(502, {'error': 'store_error', 'detail': str(e)[:300]})
        except Exception as e:
            self._send(500, {'error': 'server_error', 'detail': str(e)[:300]})

    def _route_get(self):
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

        if path.startswith('/api/auth/google/start'):
            self._handle_google_start(key)
            return

        if path.startswith('/api/auth/google/callback'):
            self._handle_google_callback(qs)
            return

        if path.startswith('/api/companies'):
            code, payload = companies_logic.list_companies(key)
        elif path.startswith('/api/invoices'):
            company_id = (qs.get('company_id') or [None])[0]
            code, payload = invoices_logic.list_invoices(key, company_id)
        elif path.startswith('/api/files'):
            file_id = (qs.get('id') or [''])[0]
            code, payload = files_logic.get_file(key, file_id)
        elif path.startswith('/api/account'):
            code, payload = users_logic.get_account(key)
        elif path.startswith('/api/admin/overview'):
            code, payload = admin_logic.get_overview(key)
        else:
            code, payload = 404, {'error': 'not_found'}

        self._send(code, payload)

    def _route_post(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            self._send(400, {'error': 'invalid_json'})
            return

        key = (body.get('key') or '').strip()

        if path.startswith('/api/auth/signup'):
            code, payload = auth_logic.signup(body.get('email'), body.get('password'), body.get('firm_name'))

        elif path.startswith('/api/auth/login'):
            code, payload = auth_logic.login(body.get('email'), body.get('password'))

        elif path.startswith('/api/account'):
            if body.get('action') == 'disconnect_gmail':
                code, payload = users_logic.disconnect_gmail(key)
            else:
                code, payload = 400, {'error': 'unknown_action'}

        elif path.startswith('/api/extract'):
            media_type = body.get('media_type') or ''
            data = body.get('data') or ''
            filename = body.get('filename') or ''
            try:
                code, payload = extract_logic.extract_invoices(key, media_type, data, filename)
            except Exception as e:
                code, payload = 500, {'error': str(e)}

        elif path.startswith('/api/files'):
            media_type = body.get('media_type') or ''
            data = body.get('data') or ''
            filename = body.get('filename') or ''
            code, payload = files_logic.save_file(key, media_type, data, filename)

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

    def _handle_google_start(self, key):
        uid = auth_logic.verify_session(key)
        if not uid:
            self._send(403, {'error': 'forbidden'})
            return
        client_id = os.environ.get('GOOGLE_CLIENT_ID')
        if not client_id:
            self._send(503, {'error': 'google_oauth_not_configured'})
            return
        redirect_uri = f"https://{self.headers.get('Host', '')}/api/auth/google/callback"
        # The session token itself doubles as the OAuth `state` - it's already
        # a signed, tamper-proof value that verify_session can check on the
        # way back, so there's no need for a second CSRF-token mechanism.
        url = google_oauth.build_auth_url(client_id, redirect_uri, state=key)
        self._redirect(url)

    def _handle_google_callback(self, qs):
        code = (qs.get('code') or [''])[0]
        state = (qs.get('state') or [''])[0]
        uid = auth_logic.verify_session(state)
        if not uid or not code:
            self._redirect('/app.html?gmail=error')
            return

        client_id = os.environ.get('GOOGLE_CLIENT_ID')
        client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
        if not client_id or not client_secret:
            self._redirect('/app.html?gmail=error')
            return
        redirect_uri = f"https://{self.headers.get('Host', '')}/api/auth/google/callback"

        try:
            tokens = google_oauth.exchange_code(client_id, client_secret, redirect_uri, code)
            refresh_token = tokens.get('refresh_token')
            if not refresh_token:
                self._redirect('/app.html?gmail=error')
                return
            profile = google_oauth.get_profile(tokens['access_token'])
            gmail_email = profile.get('emailAddress', '')
            users_logic.save_gmail_connection(uid, gmail_email, refresh_token)
            events_logic.log_event('gmail_connected', uid, users_logic.get_firm_name(uid), email=gmail_email)
            self._redirect('/app.html?gmail=connected')
        except Exception:
            self._redirect('/app.html?gmail=error')

    def _redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.end_headers()

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
